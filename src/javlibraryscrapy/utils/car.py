import logging

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
import re
import os


# 功能：发现原视频文件名中用于javbus的有码车牌
# 参数：大写后的视频文件名，素人车牌list_suren_car    示例：AVOP-127.MP4    ['LUXU', 'MIUM']
# 返回：发现的车牌    示例：AVOP-127
# 辅助：re.search
def find_car_bus(file, list_suren_car):
    """发现原视频文件名中用于 JAVBus 的车号。

    Args:
        file: 视频文件名（大小写不敏感，内部会 ``.upper()``）
        list_suren_car: 素人车厂白名单（JAVBus 上没这些厂牌的页面）

    Returns:
        车号字符串（``"ABF-340"``），无法识别返回 ``""``。

    匹配优先级：
        1. ``T28-###`` 特例
        2. ``##ID-###`` 特例（如 ``20ID-020``）
        3. **完整车号** ``[A-Z]+-\d+`` —— 优先匹配，最常见格式
        4. fallback ``[A-Z]+[-_ ]*\d\d+`` —— 允许空格/下划线（兼容旧格式）

    Bug 历史：之前 #3 fallback 顺序导致 ``madoubt.com 239929.xyz ABF-376.mp4``
    被误识别成 ``COM-239929``（前缀匹配 COM-，数字匹配 239929，忽略了真正的
    车号 ABF-376）。修复后优先匹配"字母-数字"完整车号格式。
    """
    file = file.upper()  # 兼容 javbuscar 旧行为（传小写也能识别）
    # car_pref 车牌前缀 ABP-，带横杠；car_suf，车牌后缀 123。
    # 先处理特例 T28 车牌
    if re.search(r"[^A-Z]?T28[-_ ]*\d\d+", file):
        car_pref = "T28-"
        car_suf = re.search(r"T28[-_ ]*(\d\d+)", file).group(1)
    # 以javbus上记录的20ID-020为标准
    elif re.search(r"[^\d]?\d\dID[-_ ]*\d\d+", file):
        carg = re.search(r"(\d\d)ID[-_ ]*(\d\d+)", file)
        car_pref = carg.group(1) + "ID-"
        car_suf = carg.group(2)
    # 优先匹配完整车号（字母-数字，如 ABF-376）。这个格式最常见，
    # 优先匹配避免被前面的"域名+数字"干扰（如 madoubt.com 239929.xyz ABF-376）
    elif re.search(r"[A-Z]+-\d+", file):
        carg = re.search(r"([A-Z]+)-(\d+)", file)
        car_pref = carg.group(1)
        if car_pref in list_suren_car or car_pref in [
            "HEYZO",
            "PONDO",
            "CARIB",
            "OKYOHOT",
        ]:
            return ""
        car_pref = car_pref + "-"
        car_suf = carg.group(2)
    # fallback：允许空格/下划线的车号格式
    elif re.search(r"[A-Z]+[-_ ]*\d\d+", file):
        carg = re.search(r"([A-Z]+)[-_ ]*(\d\d+)", file)
        car_pref = carg.group(1)
        if car_pref in list_suren_car or car_pref in [
            "HEYZO",
            "PONDO",
            "CARIB",
            "OKYOHOT",
        ]:
            return ""
        car_pref = car_pref + "-"
        car_suf = carg.group(2)
    else:
        return ""
    # 去掉太多的0，avop00127 => avop-127
    if len(car_suf) > 3:
        car_suf = car_suf[:-3].lstrip("0") + car_suf[-3:]
    return car_pref + car_suf


def javbuscar(root_dir):
    cars = []
    for root, dirs, files in os.walk(root_dir):
        for file in files:
            if file.lower().endswith((".mp4", ".mkv", ".avi", ".mov")):
                file_path = os.path.join(root, file)
                car = find_car_bus(file.upper(), ["LUXU", "MIUM"])
                if car:
                    logging.info(f"Found car: {car} in file: {file_path}")
                    cars.append((car, file_path))
                else:
                    logging.warning(f"No car found in file: {file_path}")
    return cars
