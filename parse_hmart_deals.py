import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from parse_safeway_deals import parse_and_save

FLYERS_DIR = os.path.join(os.path.dirname(__file__), "flyers", "hmart")


def run():
    parse_and_save("H Mart", FLYERS_DIR, "hmart")


if __name__ == "__main__":
    run()
