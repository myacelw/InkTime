#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计数据库中照片的评分分布：
1. 按照 _score_to_rating 计算的星级分布
2. memory_score 和 beauty_score 的分级统计
"""

import sqlite3
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
    - 阈值基于实际分数分布的第 N 百分位设定

    目标分布（金字塔型）：
    - 5 星：约 2-5%  （前 5% 的顶级照片）
    - 4 星：约 15-20%（前 20% 的优秀照片）
    - 3 星：约 45-50%（中等，主流）
    - 2 星：约 20-25%（一般）
    - 1 星：约 10-15%（较差）
    - 0 星：约 3-5%  （无价值）
    """
    # 加权平均：memory 占 70%，beauty 占 30%
    weighted_score = memory_score * 0.7 + beauty_score * 0.3

    # 阈值基于实际数据分布设定（634 张照片分析）：
    # 95 百分位=88.2，90 百分位=86.8，85 百分位=85.6，80 百分位=84.4
    if weighted_score >= 88:  # 前 5%
        return 5
    elif weighted_score >= 85:  # 前 20%
        return 4
    elif weighted_score >= 78:  # 中等（主流）
        return 3
    elif weighted_score >= 65:  # 一般
        return 2
    elif weighted_score >= 50:  # 较差
        return 1
    else:
        return 0


def score_to_rating_description(rating: int) -> str:
    """返回星级对应的描述"""
    descriptions = {
        5: "顶级珍藏 (88+ 分)",
        4: "优秀 (85-87 分)",
        3: "良好 (78-84 分)",
        2: "一般 (65-77 分)",
        1: "较差 (50-64 分)",
        0: "无价值 (<50 分)",
    }
    return descriptions.get(rating, "未知")


def score_to_grade_description(score: float) -> str:
    """返回分数对应的等级描述"""
    if score >= 90:
        return "S 级 (90-100 分)"
    elif score >= 80:
        return "A 级 (80-89 分)"
    elif score >= 65:
        return "B 级 (65-79 分)"
    elif score >= 50:
        return "C 级 (50-64 分)"
    elif score >= 30:
        return "D 级 (30-49 分)"
    else:
        return "E 级 (<30 分)"


def main():
    if not DB_PATH.exists():
        print(f"错误：数据库文件不存在：{DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 获取所有照片的评分
    cur.execute("SELECT path, memory_score, beauty_score FROM photo_scores")
    rows = cur.fetchall()

    if not rows:
        print("数据库中没有照片记录")
        conn.close()
        return

    total = len(rows)
    print(f"\n{'=' * 70}")
    print(f"照片评分统计报告")
    print(f"{'=' * 70}")
    print(f"数据库路径：{DB_PATH}")
    print(f"照片总数：{total}")
    print(f"{'=' * 70}\n")

    # ==================== 1. 按星级统计 ====================
    print("【一、按综合星级统计】")
    print("-" * 70)

    rating_counts = {i: 0 for i in range(6)}  # 0-5 星
    rating_photos = {i: [] for i in range(6)}

    for path, memory_score, beauty_score in rows:
        if memory_score is not None and beauty_score is not None:
            rating = _score_to_rating(memory_score, beauty_score)
            rating_counts[rating] += 1
            rating_photos[rating].append(
                {
                    "path": path,
                    "memory": memory_score,
                    "beauty": beauty_score,
                    "avg": (memory_score + beauty_score) / 2,
                }
            )

    print(f"{'星级':<8} {'描述':<25} {'数量':>8} {'占比':>10}")
    print("-" * 70)

    for rating in range(5, -1, -1):  # 从 5 星到 0 星
        count = rating_counts[rating]
        percentage = (count / total * 100) if total > 0 else 0
        desc = score_to_rating_description(rating)
        star_str = "⭐" * rating if rating > 0 else "☆"
        print(f"{star_str:<8} {desc:<25} {count:>8} {percentage:>9.1f}%")

    print("-" * 70)
    print(f"{'总计':<34} {total:>8} {100.0:>10.1f}%\n")

    # ==================== 2. memory_score 分级统计 ====================
    print("【二、按 memory_score（回忆度）分级统计】")
    print("-" * 70)

    memory_grades = {
        "S": 0,  # 90-100
        "A": 0,  # 80-89
        "B": 0,  # 65-79
        "C": 0,  # 50-64
        "D": 0,  # 30-49
        "E": 0,  # <30
    }

    for path, memory_score, beauty_score in rows:
        if memory_score is not None:
            if memory_score >= 90:
                memory_grades["S"] += 1
            elif memory_score >= 80:
                memory_grades["A"] += 1
            elif memory_score >= 65:
                memory_grades["B"] += 1
            elif memory_score >= 50:
                memory_grades["C"] += 1
            elif memory_score >= 30:
                memory_grades["D"] += 1
            else:
                memory_grades["E"] += 1

    print(f"{'等级':<8} {'描述':<25} {'数量':>8} {'占比':>10}")
    print("-" * 70)

    grade_order = ["S", "A", "B", "C", "D", "E"]
    grade_desc = {
        "S": "S 级 (90-100 分) - 极致回忆价值",
        "A": "A 级 (80-89 分) - 高度回忆价值",
        "B": "B 级 (65-79 分) - 中等回忆价值",
        "C": "C 级 (50-64 分) - 一般回忆价值",
        "D": "D 级 (30-49 分) - 较低回忆价值",
        "E": "E 级 (<30 分) - 几乎无回忆价值",
    }

    for grade in grade_order:
        count = memory_grades[grade]
        percentage = (count / total * 100) if total > 0 else 0
        desc = grade_desc[grade]
        print(f"{grade:<8} {desc:<25} {count:>8} {percentage:>9.1f}%")

    print("-" * 70)
    print(f"{'总计':<34} {total:>8} {100.0:>10.1f}%\n")

    # ==================== 3. beauty_score 分级统计 ====================
    print("【三、按 beauty_score（美观度）分级统计】")
    print("-" * 70)

    beauty_grades = {
        "S": 0,  # 90-100
        "A": 0,  # 80-89
        "B": 0,  # 65-79
        "C": 0,  # 50-64
        "D": 0,  # 30-49
        "E": 0,  # <30
    }

    for path, memory_score, beauty_score in rows:
        if beauty_score is not None:
            if beauty_score >= 90:
                beauty_grades["S"] += 1
            elif beauty_score >= 80:
                beauty_grades["A"] += 1
            elif beauty_score >= 65:
                beauty_grades["B"] += 1
            elif beauty_score >= 50:
                beauty_grades["C"] += 1
            elif beauty_score >= 30:
                beauty_grades["D"] += 1
            else:
                beauty_grades["E"] += 1

    print(f"{'等级':<8} {'描述':<25} {'数量':>8} {'占比':>10}")
    print("-" * 70)

    for grade in grade_order:
        count = beauty_grades[grade]
        percentage = (count / total * 100) if total > 0 else 0
        desc = grade_desc[grade]
        print(f"{grade:<8} {desc:<25} {count:>8} {percentage:>9.1f}%")

    print("-" * 70)
    print(f"{'总计':<34} {total:>8} {100.0:>10.1f}%\n")

    # ==================== 4. 高级统计信息 ====================
    print("【四、高级统计信息】")
    print("-" * 70)

    # 计算平均分
    memory_scores = [r[1] for r in rows if r[1] is not None]
    beauty_scores = [r[2] for r in rows if r[2] is not None]

    if memory_scores:
        avg_memory = sum(memory_scores) / len(memory_scores)
        min_memory = min(memory_scores)
        max_memory = max(memory_scores)
        print(f"memory_score（回忆度）统计:")
        print(f"  平均分：{avg_memory:.1f}")
        print(f"  最低分：{min_memory:.1f}")
        print(f"  最高分：{max_memory:.1f}")

    if beauty_scores:
        avg_beauty = sum(beauty_scores) / len(beauty_scores)
        min_beauty = min(beauty_scores)
        max_beauty = max(beauty_scores)
        print(f"\nbeauty_score（美观度）统计:")
        print(f"  平均分：{avg_beauty:.1f}")
        print(f"  最低分：{min_beauty:.1f}")
        print(f"  最高分：{max_beauty:.1f}")

    # 计算综合平均分
    combined_avgs = [
        (m + b) / 2
        for m, b in zip(memory_scores, beauty_scores)
        if m is not None and b is not None
    ]
    if combined_avgs:
        avg_combined = sum(combined_avgs) / len(combined_avgs)
        print(f"\n综合平均分（memory + beauty）/ 2: {avg_combined:.1f}")

    print("-" * 70)

    # ==================== 5. 高分照片示例 ====================
    print("\n【五、各星级高分照片示例（每档最多 5 张）】")
    print("-" * 70)

    for rating in range(5, 2, -1):  # 只显示 3 星及以上
        photos = rating_photos[rating]
        if not photos:
            continue

        # 按平均分排序
        photos_sorted = sorted(photos, key=lambda x: x["avg"], reverse=True)[:5]

        print(f"\n{rating}星级照片示例 ({score_to_rating_description(rating)}):")
        for i, photo in enumerate(photos_sorted, 1):
            path_name = Path(photo["path"]).name
            print(f"  {i}. {path_name}")
            print(f"     路径：{photo['path']}")
            print(
                f"     回忆度：{photo['memory']:.1f} | 美观度：{photo['beauty']:.1f} | 平均：{photo['avg']:.1f}"
            )

    print("\n" + "=" * 70)
    print("统计完成")
    print("=" * 70 + "\n")

    conn.close()


if __name__ == "__main__":
    main()
