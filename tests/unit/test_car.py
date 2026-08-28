"""
javlibraryscrapy.utils.car 的单元测试。

覆盖 ``find_car_bus`` 的车号识别逻辑 + 误识别回归保护。

运行：
    uv run pytest tests/unit/test_car.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把 src/ 加到 sys.path，方便直接 ``python -m`` 跑。
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from javlibraryscrapy.utils.car import find_car_bus  # noqa: E402


_LIST_SUREN_CAR = ["LUXU", "MIUM"]


# --------------------------------------------------------------------------- #
# 基本识别
# --------------------------------------------------------------------------- #
def test_recognizes_standard_car_id():
    """标准车号 ``ABF-340`` 能识别。"""
    assert find_car_bus("ABF-340.MP4", _LIST_SUREN_CAR) == "ABF-340"
    assert find_car_bus("hkbisi.com@ABF-340-C.mp4", _LIST_SUREN_CAR) == "ABF-340"
    print("✅ test_recognizes_standard_car_id")


def test_recognizes_lowercase_filename():
    """小写文件名也能识别（workflow 端负责 uppercase 调用）。"""
    assert find_car_bus("abf-340.mp4", _LIST_SUREN_CAR) == "ABF-340"
    print("✅ test_recognizes_lowercase_filename")


def test_recognizes_car_id_with_trailing_suffix():
    """带后缀的车号（``ABF-340-C``、``HMN-880-UC``、``snos-309ch``）"""
    assert find_car_bus("ABF-375_Uncensored.mp4", _LIST_SUREN_CAR) == "ABF-375"
    assert find_car_bus("HMN-880-UC.mp4", _LIST_SUREN_CAR) == "HMN-880"
    assert find_car_bus("snos-309ch.mp4", _LIST_SUREN_CAR) == "SNOS-309"
    print("✅ test_recognizes_car_id_with_trailing_suffix")


def test_recognizes_t28_special():
    """T28 特例车牌。"""
    assert find_car_bus("T28-123.MP4", _LIST_SUREN_CAR) == "T28-123"
    print("✅ test_recognizes_t28_special")


def test_recognizes_id_special():
    """``20ID-020`` 格式特例外。"""
    assert find_car_bus("20ID-020.mp4", _LIST_SUREN_CAR) == "20ID-020"
    print("✅ test_recognizes_id_special")


def test_recognizes_car_id_with_underscore_separator():
    """下划线分隔的车号（如 ``SNOS_309ch``）。"""
    assert find_car_bus("SNOS_309ch.mp4", _LIST_SUREN_CAR) == "SNOS-309"
    print("✅ test_recognizes_car_id_with_underscore_separator")


# --------------------------------------------------------------------------- #
# 误识别回归保护
# --------------------------------------------------------------------------- #
def test_does_not_misidentify_domain_as_car():
    """**关键回归**：``madoubt.com 239929.xyz ABF-376`` 必须识别成 ABF-376
    而不是 ``COM-239929``（之前 fallback 顺序导致误识别）。
    """
    # 域名 + 中间随机数字 + 末尾真车号
    assert find_car_bus("madoubt.com 239929.xyz ABF-376.mp4", _LIST_SUREN_CAR) == "ABF-376"
    # 类似格式
    assert find_car_bus("somesite.com 12345 ABF-340.mp4", _LIST_SUREN_CAR) == "ABF-340"
    print("✅ test_does_not_misidentify_domain_as_car")


def test_does_not_misidentify_at_prefix():
    """``hkbisi.com@HMN-880-UC`` 必须识别成 HMN-880，不是其它。"""
    assert find_car_bus("hkbisi.com@HMN-880-UC.mp4", _LIST_SUREN_CAR) == "HMN-880"
    print("✅ test_does_not_misidentify_at_prefix")


def test_does_not_misidentify_random_at_front():
    """``xxx.com 99999 ABF-376`` 这种"前面是噪声"格式，必须识别后面真车号。"""
    assert find_car_bus("xxx.com 99999 ABF-376.mp4", _LIST_SUREN_CAR) == "ABF-376"
    assert find_car_bus("xyz.net 123 PPPE-435.mp4", _LIST_SUREN_CAR) == "PPPE-435"
    print("✅ test_does_not_misidentify_random_at_front")


# --------------------------------------------------------------------------- #
# 黑名单厂牌
# --------------------------------------------------------------------------- #
def test_excluded_car_ids_return_empty():
    """黑名单厂牌（HEYZO/PONDO/CARIB/OKYOHOT）返回空（JAVBus 上没页面）。"""
    assert find_car_bus("HEYZO-1234.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("PONDO-567.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("CARIB-890.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("OKYOHOT-100.mp4", _LIST_SUREN_CAR) == ""
    print("✅ test_excluded_car_ids_return_empty")


def test_list_suren_car_ids_return_empty():
    """白名单厂牌（LUXU/MIUM）也返回空（调用方传进 list_suren_car）。"""
    assert find_car_bus("LUXU-200.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("MIUM-300.mp4", _LIST_SUREN_CAR) == ""
    print("✅ test_list_suren_car_ids_return_empty")


# --------------------------------------------------------------------------- #
# 无匹配
# --------------------------------------------------------------------------- #
def test_returns_empty_for_non_car_filename():
    """完全不包含车号格式的文件名返回空。"""
    assert find_car_bus("random.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("家庭录像 2024.mp4", _LIST_SUREN_CAR) == ""
    assert find_car_bus("no-extension", _LIST_SUREN_CAR) == ""
    print("✅ test_returns_empty_for_non_car_filename")


# --------------------------------------------------------------------------- #
# 去掉太多的 0
# --------------------------------------------------------------------------- #
def test_strips_leading_zeros():
    """``avop00127`` → ``avop-127``（去掉前导 0，保留后 3 位）。"""
    assert find_car_bus("AVOP00127.mp4", _LIST_SUREN_CAR) == "AVOP-127"
    print("✅ test_strips_leading_zeros")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    test_recognizes_standard_car_id()
    test_recognizes_lowercase_filename()
    test_recognizes_car_id_with_trailing_suffix()
    test_recognizes_t28_special()
    test_recognizes_id_special()
    test_recognizes_car_id_with_underscore_separator()
    test_does_not_misidentify_domain_as_car()
    test_does_not_misidentify_at_prefix()
    test_does_not_misidentify_random_at_front()
    test_excluded_car_ids_return_empty()
    test_list_suren_car_ids_return_empty()
    test_returns_empty_for_non_car_filename()
    test_strips_leading_zeros()
    print("\n🎉 ALL TESTS PASSED")
