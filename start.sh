#!/bin/bash
# 每次容器啟動時，先在背景跑籌碼資料抓取，再啟動 Streamlit
python -m chip_module.fetch_daily &
streamlit run main.py --server.port $PORT --server.address 0.0.0.0
