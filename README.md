# catalog_downloader
a simple roblox downloader that only downloads catalog assets created by Roblox.

## how to use
you can run this system on your computer or on a server but it will take around 45 min to an hour. it will require python3 and pip3 and you must install lz4 and DracoPy using these commands: `pip3 install requests lz4`, `pip3 install DracoPy`.

run eather using `python roblox_asset_downloader.py` or `python3 roblox_asset_downloader.py`.
for code spaces use `nohup python3 roblox_asset_downloader.py > output.log 2>&1 &`.

you have to set up the cache to download hair accessorys or it will not work. before running type `export ROBLOSECURITY="_|WARNING:-DO-NOT..."`, replace everything inside the "" with your account / alt account cache inside.

to refresh and add new items here are the commands below:

```
# refresh everything
python3 roblox_asset_downloader.py --refresh-all

# refresh specific sections
python3 roblox_asset_downloader.py --refresh-accessories
python3 roblox_asset_downloader.py --refresh-offsale
python3 roblox_asset_downloader.py --refresh-gears
python3 roblox_asset_downloader.py --refresh-bundles
```

also it is reconmended to stop the program on codespaces to type this command: `pkill -f roblox_asset_downloader.py`

## thx for using
thx for using, i don have it made to download classic faces but you can use ai to explain how it works. please report any issues so i can fix them.