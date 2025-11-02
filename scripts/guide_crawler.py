"""批量爬取攻略并存储到Chroma 1.x"""
from rag.processor import TripRAG
from typing import List
import time
import random
from urllib.parse import urlparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def _is_valid_url(url: str) -> bool:
    """验证URL有效性"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def batch_crawl_guides(urls: List[str], delay_range: tuple = (3, 6)) -> None:
    """批量爬取攻略链接"""
    rag = TripRAG()
    valid_urls = [url for url in urls if _is_valid_url(url)]
    logger.info(f"开始解析 {len(valid_urls)} 个有效链接...")

    for i, url in enumerate(valid_urls, 1):
        try:
            logger.info(f"进度 {i}/{len(valid_urls)}: {url}")
            rag.load_and_store_guides([url])
            delay = random.uniform(*delay_range)
            logger.info(f"等待 {delay:.1f} 秒后继续...")
            time.sleep(delay)
        except Exception as e:
            logger.error(f"解析失败 {url}：{str(e)}")
            continue

    logger.info("批量解析完成，数据已存入Chroma向量库")

if __name__ == "__main__":
    test_urls = [
        "https://www.mafengwo.cn/i/23884996.html",
        "https://you.ctrip.com/travels/chengdu104/3886621.html",
        "https://www.xiaohongshu.com/discovery/item/64d2f10000000003002e8b"
    ]
    batch_crawl_guides(test_urls)