def host_context(session="stream-a", platform="qq", group="123", user="10001", account=""):
    return {
        "contract": "neko.tools.context.v1",
        "session_id": session,
        "stream_id": session,
        "platform": platform,
        "group_id": group,
        "user_id": user,
        "account_id": account,
    }
