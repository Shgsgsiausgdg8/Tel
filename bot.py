import asyncio
import main
import admin_bot

async def main():
    # اجرای همزمان هر دو فایل
    await asyncio.gather(
        main.main(),         # اجرای main از main.py
        admin_bot.main()     # اجرای main از admin_bot.py
    )

# اجرای همزمان با asyncio.run
asyncio.run(main())