# --- 启动逻辑 ---
async def main():
    init_db()
    # 1. 强制重置 Webhook 状态
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 2. 打印机器人信息，如果你在日志里看不到下面这句话，说明机器人没连上服务器
    me = await bot.get_me()
    print(f"--- 机器人认证成功: @{me.username} ---")
    print(f"--- 当前管理员名单: {ADMIN_IDS} ---")

    # 3. 配置 Web 服务
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, loop="asyncio")
    server = uvicorn.Server(config)
    
    # 4. 同时启动机器人轮询和 Web 服务器
    # 注意：不要用 start_polling 的普通运行方式，用下面的 gather
    await asyncio.gather(
        dp.start_polling(bot),
        server.serve()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"--- 严重错误: {e} ---")
