import asyncio
import bot
import admin_bot

async def main():
    # هر دو فایل را به صورت همزمان اجرا می‌کنیم
    await asyncio.gather(
        bot.main(),         # اجرا کردن main از bot.py
        admin_bot.main()    # اجرا کردن main از admin_bot.py
    )

# در اینجا از asyncio.run استفاده می‌کنیم تا همزمان اجرا بشن
asyncio.run(main())