#!/bin/bash

# اسکریپت نصب پیش‌نیازهای ربات پشتیبانی تلگرامی برای ترموکس
# تاریخ: 06 می 2025
# توضیح: این اسکریپت پکیج‌های سیستمی و کتابخانه‌های پایتون را نصب می‌کند

# تنظیم رنگ‌ها برای خروجی
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # بدون رنگ

# تابع بررسی خطا
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}خطا: $1${NC}"
        exit 1
    fi
}

# به‌روزرسانی پکیج‌ها
echo -e "${YELLOW}به‌روزرسانی پکیج‌های ترموکس...${NC}"
pkg update -y && pkg upgrade -y
check_error "به‌روزرسانی پکیج‌ها ناموفق بود"

# نصب پکیج‌های سیستمی
echo -e "${YELLOW}نصب پکیج‌های سیستمی...${NC}"
pkg install python -y
check_error "نصب پایتون ناموفق بود"
pkg install redis -y
check_error "نصب Redis ناموفق بود"
pkg install git -y
check_error "نصب Git ناموفق بود"
pkg install wget -y
check_error "نصب wget ناموفق بود"

# نصب pip
echo -e "${YELLOW}نصب pip...${NC}"
python -m ensurepip --upgrade
check_error "نصب pip ناموفق بود"
python -m pip install --upgrade pip
check_error "به‌روزرسانی pip ناموفق بود"

# نصب کتابخانه‌های پایتون
echo -e "${YELLOW}نصب کتابخانه‌های پایتون...${NC}"
pip install telethon
check_error "نصب telethon ناموفق بود"
pip install aiohttp
check_error "نصب aiohttp ناموفق بود"
pip install fuzzywuzzy
check_error "نصب fuzzywuzzy ناموفق بود"
pip install python-Levenshtein
check_error "نصب python-Levenshtein ناموفق بود"
pip install redis
check_error "نصب redis-py ناموفق بود"
pip install transformers
check_error "نصب transformers ناموفق بود"
pip install onnxruntime
check_error "نصب onnxruntime ناموفق بود"
pip install pandas
check_error "نصب pandas ناموفق بود"
pip install datasets
check_error "نصب datasets ناموفق بود"
pip install torch
check_error "نصب torch ناموفق بود"

# راه‌اندازی Redis
echo -e "${YELLOW}راه‌اندازی سرور Redis...${NC}"
redis-server --daemonize yes
if [ $? -eq 0 ]; then
    echo -e "${GREEN}Redis با موفقیت راه‌اندازی شد${NC}"
else
    echo -e "${RED}خطا در راه‌اندازی Redis. لطفاً به‌صورت دستی اجرا کنید: redis-server --daemonize yes${NC}"
fi

# بررسی نصب
echo -e "${YELLOW}بررسی نصب پکیج‌ها...${NC}"
python -c "import telethon, aiohttp, fuzzywuzzy, redis, transformers, onnxruntime, pandas, datasets, torch"
check_error "یکی از کتابخانه‌های پایتون نصب نشده است"

echo -e "${GREEN}نصب پیش‌نیازها با موفقیت انجام شد!${NC}"
echo -e "${YELLOW}مراحل بعدی:${NC}"
echo "1. توکن‌های ربات را در bot.py و admin_bot.py تنظیم کنید."
echo "2. فایل responses.json را در کنار bot.py قرار دهید."
echo "3. مدل آفلاین را با train_model.py تولید کنید (ترجیحاً روی سرور قوی‌تر)."
echo "4. ربات را اجرا کنید: python bot.py و python admin_bot.py"