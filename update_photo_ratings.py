#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量更新照片元数据：
- EXIF:Rating + XMP:Rating: 使用新的 _score_to_rating 算法
- XMP:Description: 一句话文案
"""

import sqlite3
import subprocess
from pathlib import Path
import config as cfg

# 数据库路径
ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = Path(str(getattr(cfg, "DB_PATH", "photos.db") or "photos.db")).expanduser()
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT_DIR / DB_PATH).resolve()


def _score_to_rating(memory_score: float, beauty_score: float) -> int:
    """将记忆分和美观分转换为 0-5 星的 Rating。

    采用加权平均 + 动态分段映射，使星级分布呈金字塔型：
    - 记忆分权重 70%，美观分权重 30%（回忆价值更重要）
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


def update_metadata(path: str, rating: int, side_caption: str | None = None) -> bool:
    """使用 exiftool 更新单张照片的元数据

    同时写入：
    - EXIF:Rating + XMP:Rating: 综合评分（双标准兼容）
    - XMP:Description: 一句话文案（如果提供）
    """
    try:
        cmd = [
            "exiftool",
            "-overwrite_original",
            f"-EXIF:Rating={rating}",
            f"-XMP:Rating={rating}",
        ]

        # 写入一句话文案到 XMP:Description
        if side_caption:
            cmd.append(f"-XMP:Description={side_caption}")

        cmd.append(path)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def main():
    if not DB_PATH.exists():
        print(f"错误：数据库文件不存在：{DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 获取所有照片的评分和一句话文案
    cur.execute(
        "SELECT path, memory_score, beauty_score, side_caption FROM photo_scores WHERE memory_score IS NOT NULL AND beauty_score IS NOT NULL"
    )
    rows = cur.fetchall()

    if not rows:
        print("数据库中没有可更新的照片记录")
        conn.close()
        return

    total = len(rows)
    print(f"\n{'=' * 70}")
    print(f"批量更新照片元数据 (Rating + XMP:Description)")
    print(f"{'=' * 70}")
    print(f"数据库路径：{DB_PATH}")
    print(f"待更新照片数：{total}")
    print(f"{'=' * 70}\n")

    # 统计各星级数量
    rating_counts = {i: 0 for i in range(6)}
    success_count = 0
    fail_count = 0
    skipped_count = 0

    print("开始更新...\n")

    for idx, (path, memory_score, beauty_score, side_caption) in enumerate(rows, 1):
        # 计算新 rating
        new_rating = _score_to_rating(memory_score, beauty_score)
        rating_counts[new_rating] += 1

        # 进度显示
        if idx % 50 == 0 or idx == 1:
            print(f"[{idx}/{total}] 处理中...")

        # 检查文件是否存在
        if not Path(path).exists():
            skipped_count += 1
            continue

        # 更新元数据（Rating + XMP:Description）
        if update_metadata(path, new_rating, side_caption):
            success_count += 1
        else:
            fail_count += 1

    conn.close()

    # 输出统计
    print(f"\n{'=' * 70}")
    print(f"更新完成")
    print(f"{'=' * 70}")
    print(f"成功：{success_count} 张")
    print(f"失败：{fail_count} 张")
    print(f"跳过（文件不存在）：{skipped_count} 张")
    print(f"\n星级分布:")

    for rating in range(5, -1, -1):
        count = rating_counts[rating]
        percentage = (count / total * 100) if total > 0 else 0
        star_str = "⭐" * rating if rating > 0 else "☆"
        print(f"  {star_str} {rating}星：{count} 张 ({percentage:.1f}%)")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    main()
