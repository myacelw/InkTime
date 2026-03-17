#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将数据库中 5 星照片同步到 Immich 标记为收藏

功能：
1. 从 photos.db 中读取 rating=5 的照片路径
2. 将路径前缀从 "/Volumes/KIOXIA/photos/" 转换为 Immich 中的路径
3. 调用 Immich API 查询图片 ID
4. 调用 Immich API 标记为收藏
"""

import sqlite3
import requests
from pathlib import Path
import config as cfg
from typing import Optional, Tuple, List

# ==================== 配置区域 ====================

# Immich 服务器地址和 API Key
IMMICH_SERVER = str(getattr(cfg, "IMMICH_SERVER", "http://localhost:2283")).rstrip("/")
IMMICH_API_KEY = str(getattr(cfg, "IMMICH_API_KEY", "")).strip()

# 路径前缀映射：本地路径 -> Immich 路径
# 本地：/Volumes/KIOXIA/photos/2018-10 日本/DSC_0781.JPG
# Immich: ext_photos/2018-10 日本/DSC_0781.JPG
LOCAL_PATH_PREFIX = str(
    getattr(cfg, "LOCAL_PATH_PREFIX", "/Volumes/KIOXIA/photos")
).rstrip("/")
IMMICH_PATH_PREFIX = str(getattr(cfg, "IMMICH_PATH_PREFIX", "ext_photos")).lstrip("/")

# 数据库路径
ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(str(getattr(cfg, "DB_PATH", "photos.db") or "photos.db")).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT_DIR / DB_PATH).resolve()

# ==================== 辅助函数 ====================


def _score_to_rating(memory_score: float, beauty_score: float) -> int:
    """将记忆分和美观分转换为 0-5 星的 Rating。

    采用加权平均 + 动态分段映射，使星级分布呈金字塔型：
    - 记忆分权重 70%，美观分权重 30%（回忆价值更重要）
    - 阈值基于实际数据分布的第 N 百分位设定
    """
    weighted_score = memory_score * 0.7 + beauty_score * 0.3

    if weighted_score >= 88:
        return 5
    elif weighted_score >= 85:
        return 4
    elif weighted_score >= 78:
        return 3
    elif weighted_score >= 65:
        return 2
    elif weighted_score >= 50:
        return 1
    else:
        return 0


# ==================== 辅助函数 ====================


def get_db_connection() -> sqlite3.Connection:
    """获取数据库连接"""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"数据库文件不存在：{DB_PATH}")
    return sqlite3.connect(DB_PATH)


def local_to_immich_path(local_path: str) -> str:
    """
    将本地路径转换为 Immich 路径

    本地：/Volumes/KIOXIA/photos/2018-10 日本/DSC_0781.JPG
    Immich: ext_photos/2018-10 日本/DSC_0781.JPG
    """
    if local_path.startswith(LOCAL_PATH_PREFIX):
        relative = local_path[len(LOCAL_PATH_PREFIX) :].lstrip("/")
        return f"{IMMICH_PATH_PREFIX}/{relative}"
    return local_path


def find_asset_by_path(
    session: requests.Session, original_path: str
) -> Tuple[Optional[str], Optional[bool]]:
    """
    根据原始路径查找 Immich 中的资产 ID

    Args:
        session: requests Session 对象（复用连接）
        original_path: Immich 中的原始路径（如：ext_photos/2018-10 日本/DSC_0781.JPG）

    Returns:
        Tuple[资产 ID, 是否已收藏]，如果未找到则返回 (None, None)
    """
    url = f"{IMMICH_SERVER}/api/search/metadata"
    params = {"apiKey": IMMICH_API_KEY}
    payload = {"originalPath": original_path}

    try:
        resp = session.post(url, json=payload, params=params, timeout=30)
        resp.raise_for_status()

        data = resp.json()
        assets = data.get("assets", {})
        items = assets.get("items", [])

        if items:
            asset_id = items[0].get("id")
            is_favorite = items[0].get("isFavorite", False)
            return asset_id, is_favorite
        return None, None

    except requests.exceptions.RequestException as e:
        print(f"  [ERROR] 查询资产失败：{e}")
        return None, None
    except Exception as e:
        print(f"  [ERROR] 解析响应失败：{e}")
        return None, None


def set_asset_favorite(
    session: requests.Session, asset_id: str, is_favorite: bool = True
) -> bool:
    """
    设置资产为收藏状态

    Args:
        session: requests Session 对象
        asset_id: 资产 ID（UUID）
        is_favorite: 是否收藏（默认 True）

    Returns:
        是否成功
    """
    url = f"{IMMICH_SERVER}/api/assets/{asset_id}"
    params = {"apiKey": IMMICH_API_KEY}
    payload = {"isFavorite": is_favorite}

    try:
        resp = session.put(url, json=payload, params=params, timeout=30)
        resp.raise_for_status()
        return True

    except requests.exceptions.RequestException as e:
        print(f"  [ERROR] 设置收藏失败：{e}")
        return False
    except Exception as e:
        print(f"  [ERROR] 请求失败：{e}")
        return False


def get_rating_from_exif(path: str) -> int:
    """
    从照片 EXIF 中读取 Rating 字段

    Args:
        path: 照片本地路径

    Returns:
        Rating 值（0-5），如果读取失败返回 -1
    """
    import subprocess

    try:
        result = subprocess.run(
            ["exiftool", "-EXIF:Rating", "-XMP:Rating", "-s", "-s", "-s", path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            # 取第一个非空的 Rating 值
            for line in result.stdout.strip().split("\n"):
                if line.strip().isdigit():
                    return int(line.strip())
        return -1
    except Exception:
        return -1


# ==================== 主流程 ====================


def sync_favorites(dry_run: bool = False, force_rescan: bool = False):
    """
    同步 5 星照片到 Immich 收藏

    Args:
        dry_run: 如果为 True，只显示不执行实际操作
        force_rescan: 如果为 True，强制重新从 EXIF 读取 rating（忽略数据库）
    """
    if not IMMICH_API_KEY:
        print("[ERROR] 未配置 IMMICH_API_KEY，请在 config.py 中设置")
        print("示例：")
        print("  IMMICH_SERVER = 'http://localhost:2283'")
        print("  IMMICH_API_KEY = '你的 API Key'")
        return

    print("=" * 70)
    print("Immich 收藏同步工具")
    print("=" * 70)
    print(f"服务器：{IMMICH_SERVER}")
    print(f"数据库：{DB_PATH}")
    print(f"本地前缀：{LOCAL_PATH_PREFIX}")
    print(f"Immich 前缀：{IMMICH_PATH_PREFIX}")
    print(f"模式：{'干运行（不实际修改）' if dry_run else '实际执行'}")
    print(f"强制重扫：{'是' if force_rescan else '否（使用数据库评分）'}")
    print("=" * 70)

    # 获取数据库连接
    conn = get_db_connection()
    cur = conn.cursor()

    if force_rescan:
        # 强制模式：只查询路径，从 EXIF 重新读取 rating（慢）
        cur.execute(
            "SELECT path FROM photo_scores WHERE path LIKE ?",
            (LOCAL_PATH_PREFIX + "%",),
        )
        all_paths = [(row[0],) for row in cur.fetchall()]
        print(f"\n数据库中共有 {len(all_paths)} 张照片（当前前缀下）")
        print("强制模式：正在从 EXIF 重新读取 rating 信息，请稍候...\n")

        # 从 EXIF 读取 rating
        five_star_photos = []
        rating_stats = {i: 0 for i in range(6)}

        for idx, (path,) in enumerate(all_paths, 1):
            if idx % 100 == 0:
                print(f"  处理进度：{idx}/{len(all_paths)} ...")

            rating = get_rating_from_exif(path)
            if rating >= 0:
                rating_stats[rating] += 1
                if rating == 5:
                    five_star_photos.append(path)

        print(f"\n星级分布:")
        for star in range(5, -1, -1):
            count = rating_stats[star]
            if count > 0:
                print(f"  {'⭐' * star if star > 0 else '☆'} {star}星：{count} 张")

    else:
        # 普通模式：从数据库读取 memory_score 和 beauty_score，计算 rating（快）
        cur.execute(
            "SELECT path, memory_score, beauty_score FROM photo_scores WHERE path LIKE ? AND memory_score IS NOT NULL AND beauty_score IS NOT NULL",
            (LOCAL_PATH_PREFIX + "%",),
        )
        rows = cur.fetchall()
        conn.close()

        print(f"\n数据库中共有 {len(rows)} 张照片（当前前缀下，有评分）")
        print("使用数据库评分计算 rating...\n")

        # 使用 _score_to_rating 计算 rating
        five_star_photos = []
        rating_stats = {i: 0 for i in range(6)}

        for path, memory_score, beauty_score in rows:
            rating = _score_to_rating(memory_score, beauty_score)
            rating_stats[rating] += 1
            if rating == 5:
                five_star_photos.append(path)

        print(f"星级分布:")
        for star in range(5, -1, -1):
            count = rating_stats[star]
            if count > 0:
                print(f"  {'⭐' * star if star > 0 else '☆'} {star}星：{count} 张")

    print(f"\n发现 {len(five_star_photos)} 张 5 星照片待同步到 Immich\n")

    if not five_star_photos:
        print("没有需要同步的照片。")
        return

    # 创建复用连接的 session
    with requests.Session() as session:
        success_count = 0
        already_favorite_count = 0
        not_found_count = 0
        error_count = 0

        for idx, local_path in enumerate(five_star_photos, 1):
            print(f"[{idx}/{len(five_star_photos)}] {local_path}")

            # 转换为 Immich 路径
            immich_path = local_to_immich_path(local_path)
            print(f"  → Immich 路径：{immich_path}")

            # 查询资产 ID
            asset_id, is_favorite = find_asset_by_path(session, immich_path)

            if asset_id is None:
                print(f"  ⚠️  未在 Immich 中找到该照片")
                not_found_count += 1
                continue

            print(f"  ✓ 找到资产 ID: {asset_id}")
            print(f"  当前收藏状态：{'已收藏' if is_favorite else '未收藏'}")

            if is_favorite:
                already_favorite_count += 1
                continue

            # 设置为收藏
            if dry_run:
                print(f"  [DRY RUN] 将标记为收藏")
                success_count += 1
            else:
                if set_asset_favorite(session, asset_id, is_favorite=True):
                    print(f"  ✓ 已标记为收藏")
                    success_count += 1
                else:
                    error_count += 1

            print()

    # 输出统计
    print("=" * 70)
    print("同步完成")
    print("=" * 70)
    print(f"成功标记：{success_count} 张")
    print(f"已是收藏：{already_favorite_count} 张")
    print(f"未找到：{not_found_count} 张")
    print(f"失败：{error_count} 张")
    print("=" * 70)
    print("Immich 收藏同步工具")
    print("=" * 70)
    print(f"服务器：{IMMICH_SERVER}")
    print(f"数据库：{DB_PATH}")
    print(f"本地前缀：{LOCAL_PATH_PREFIX}")
    print(f"Immich 前缀：{IMMICH_PATH_PREFIX}")
    print(f"模式：{'干运行（不实际修改）' if dry_run else '实际执行'}")
    print("=" * 70)

    # 获取数据库连接
    conn = get_db_connection()
    cur = conn.cursor()

    # 查询所有有评分的照片
    if force_rescan:
        # 强制模式：查询所有照片，从 EXIF 重新读取 rating
        cur.execute(
            "SELECT path FROM photo_scores WHERE path LIKE ?",
            (LOCAL_PATH_PREFIX + "%",),
        )
    else:
        # 普通模式：从数据库读取（EXIF 应该已经更新过）
        # 注意：photo_scores 表没有 rating 字段，需要从 EXIF 读取
        # 这里我们先获取所有照片，然后从 EXIF 读取 rating
        cur.execute(
            "SELECT path FROM photo_scores WHERE path LIKE ?",
            (LOCAL_PATH_PREFIX + "%",),
        )

    all_paths = [row[0] for row in cur.fetchall()]
    conn.close()

    print(f"\n数据库中共有 {len(all_paths)} 张照片（当前前缀下）")
    print("正在从 EXIF 读取 rating 信息，请稍候...\n")

    # 筛选 rating=5 的照片
    five_star_photos = []
    rating_stats = {i: 0 for i in range(6)}

    for idx, path in enumerate(all_paths, 1):
        if idx % 100 == 0:
            print(f"  处理进度：{idx}/{len(all_paths)} ...")

        rating = get_rating_from_exif(path)
        if rating >= 0:
            rating_stats[rating] += 1
            if rating == 5:
                five_star_photos.append(path)

    print(f"\n星级分布:")
    for star in range(5, -1, -1):
        count = rating_stats[star]
        if count > 0:
            print(f"  {'⭐' * star if star > 0 else '☆'} {star}星：{count} 张")

    print(f"\n发现 {len(five_star_photos)} 张 5 星照片待同步到 Immich\n")

    if not five_star_photos:
        print("没有需要同步的照片。")
        return

    # 创建复用连接的 session
    with requests.Session() as session:
        success_count = 0
        already_favorite_count = 0
        not_found_count = 0
        error_count = 0

        for idx, local_path in enumerate(five_star_photos, 1):
            print(f"[{idx}/{len(five_star_photos)}] {local_path}")

            # 转换为 Immich 路径
            immich_path = local_to_immich_path(local_path)
            print(f"  → Immich 路径：{immich_path}")

            # 查询资产 ID
            asset_id, is_favorite = find_asset_by_path(session, immich_path)

            if asset_id is None:
                print(f"  ⚠️  未在 Immich 中找到该照片")
                not_found_count += 1
                continue

            print(f"  ✓ 找到资产 ID: {asset_id}")
            print(f"  当前收藏状态：{'已收藏' if is_favorite else '未收藏'}")

            if is_favorite:
                already_favorite_count += 1
                continue

            # 设置为收藏
            if dry_run:
                print(f"  [DRY RUN] 将标记为收藏")
                success_count += 1
            else:
                if set_asset_favorite(session, asset_id, is_favorite=True):
                    print(f"  ✓ 已标记为收藏")
                    success_count += 1
                else:
                    error_count += 1

            print()

    # 输出统计
    print("=" * 70)
    print("同步完成")
    print("=" * 70)
    print(f"成功标记：{success_count} 张")
    print(f"已是收藏：{already_favorite_count} 张")
    print(f"未找到：{not_found_count} 张")
    print(f"失败：{error_count} 张")
    print("=" * 70)


def main():
    import sys

    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    force_rescan = "--force" in sys.argv or "-f" in sys.argv

    if "--help" in sys.argv or "-h" in sys.argv:
        print("用法：python3 sync_to_immich.py [选项]")
        print()
        print("选项:")
        print("  -n, --dry-run   干运行，只显示不执行实际操作")
        print("  -f, --force     强制重新从 EXIF 读取 rating（忽略数据库缓存）")
        print("  -h, --help      显示帮助信息")
        print()
        print("示例:")
        print("  python3 sync_to_immich.py              # 实际执行同步")
        print("  python3 sync_to_immich.py --dry-run    # 预览将要同步的照片")
        print("  python3 sync_to_immich.py --force      # 强制重新读取 EXIF")
        return

    sync_favorites(dry_run=dry_run, force_rescan=force_rescan)


if __name__ == "__main__":
    main()
