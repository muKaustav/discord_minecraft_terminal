from mcrcon import MCRcon

try:
    print("Connecting to RCON...")
    with MCRcon("141.148.217.100", "iamyourfather", 25585) as mcr:
        print("Connected! Sending 'list' command...")
        resp = mcr.command("list")
        print(f"Response: {resp}")
    print("Disconnected successfully")
except Exception as e:
    print(f"Error: {e}")
