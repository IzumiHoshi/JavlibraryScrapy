"""画廊服务层：状态与业务逻辑。"""

from .library import GalleryState, load_movies, normalize_path_for_compare
from .covers import fetch_cover, find_local_cover, open_in_explorer

__all__ = [
    "GalleryState",
    "load_movies",
    "normalize_path_for_compare",
    "fetch_cover",
    "find_local_cover",
    "open_in_explorer",
]