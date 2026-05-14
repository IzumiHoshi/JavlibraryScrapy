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
    # 一般车牌
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
