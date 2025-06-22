"""
Author: izumihoshi
Date: 2025-06-16 22:48:30
LastEditors: izumihoshi
LastEditTime: 2025-06-16 22:49:26
FilePath: \JavlibraryScrapy\main.py
Description: TODO

Copyright (c) 2025 by Honor, All Rights Reserved.
"""

from car import javbuscar
from javbus import JavbusScraper

if __name__ == "__main__":
    cars = javbuscar(r"Y:\UnScraper")  # 替换为实际的视频目录路径
    print(f"Found {len(cars)} cars:")
    scraper = JavbusScraper()
    for car, path in cars:
        print(f"Car: {car}, Path: {path}")
        try:
            scraper.test_get_movie_info(car)
        except Exception as e:
            print("发生错误：", e)
            break
    scraper.tearDown()
