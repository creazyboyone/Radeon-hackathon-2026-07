from modelscope.hub.api import HubApi
api = HubApi()
try:
    files = api.list_repo_files("Jackrong/Qwopus3.6-27B-v2-MTP-GGUF")
    for f in files:
        print(f)
except Exception as e:
    print("ERROR:", e)
