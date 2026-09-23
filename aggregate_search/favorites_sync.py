"""Platform-neutral remote-favourite import."""
from __future__ import annotations
import asyncio, hashlib, json, time
from dataclasses import dataclass
from typing import Any, Protocol
from aggregate_search.adapters.bilibili import BilibiliAdapter
from aggregate_search.adapters.douyin import DouyinAdapter
from aggregate_search.adapters.xhs import XhsAdapter
from aggregate_search.adapters.zhihu import ZhihuAdapter
from api.services.remote_sync_state import SyncStateError

PAGE_SIZE, REQUEST_TIMEOUT, INCREMENTAL_STOP_RUN = 20, 30.0, 5
class FavoritesSyncError(ValueError):
    safe_message = "收藏列表发生变化或返回异常，已保留进度；请稍后重新同步"
@dataclass
class FavoritePage:
    results:list[dict]; fingerprint:str; complete:bool; count:int; identities:list[tuple[str,object]]; next_token:object=None
class FavoritesSource(Protocol):
    platform:str; incremental_verified:bool
    async def identity(self)->str: ...
    async def folders(self, account:str)->list[dict]: ...
    async def page(self, folder:dict, token:object)->FavoritePage: ...

class _Source:
    incremental_verified=True
    def __init__(self, client, interval=2.0):
        self.client,self.interval,self.last_request=client,interval,0.0; client.favorites_sync=True
    async def request(self, method,*args,**kwargs):
        await asyncio.sleep(max(0,self.last_request+self.interval-time.monotonic())); self.last_request=time.monotonic()
        return await asyncio.wait_for(method(*args,**kwargs),timeout=REQUEST_TIMEOUT)
    def make_page(self,rows,folder,complete,next_token,identity):
        if not isinstance(rows,list): raise FavoritesSyncError("Invalid favorite page")
        results=[]; identities=[]
        for row in rows:
            if not isinstance(row,dict): raise FavoritesSyncError("Invalid favorite item")
            converted=self.adapter.adapt([{**row,"_collection_name":folder["name"]}])
            if len(converted)!=1: raise FavoritesSyncError("Unsupported favorite item")
            result=converted[0].model_dump(mode="json"); result["metrics_status"]="partial"
            results.append(result); identities.append((identity(row,result),row.get("fav_time") or row.get("updated_time") or ""))
        if len({x[0] for x in identities}) != len(identities): raise FavoritesSyncError("Duplicate favorite items")
        fp=hashlib.sha256(json.dumps(identities,ensure_ascii=False).encode()).hexdigest()
        return FavoritePage(results,fp,bool(complete),len(rows),identities,next_token)

class BilibiliFavoritesSource(_Source):
    platform="bilibili"; adapter=BilibiliAdapter()
    async def identity(self):
        nav=await self.request(self.client.get,"/x/web-interface/nav",enable_params_sign=False)
        if not isinstance(nav,dict) or not nav.get("mid"):
            from base.exceptions import LoginRequiredError; raise LoginRequiredError(platform="bilibili",message="B站登录状态已失效")
        return f"bilibili:{int(nav['mid'])}"
    async def folders(self,account):
        data=await self.request(self.client.get_created_favorite_folders,int(account.split(":",1)[1])); rows=data.get("list") if isinstance(data,dict) else None
        if rows is None and isinstance(data,dict) and data.get("count")==0:return []
        if not isinstance(rows,list):raise FavoritesSyncError("Invalid favorite folders")
        if any(not isinstance(x,dict) or not x.get("id") for x in rows): raise FavoritesSyncError("Invalid favorite folder")
        folders=[{"id":str(x["id"]),"name":str(x.get("title") or "默认收藏夹")} for x in rows]
        if len({x["id"] for x in folders}) != len(folders): raise FavoritesSyncError("Duplicate favorite folders")
        return folders
    async def page(self,folder,token):
        number=int(token or 1); data=await self.request(self.client.get_favorite_folder_contents,int(folder["id"]),number,PAGE_SIZE)
        if not isinstance(data,dict) or data.get("has_more") not in (True,False,0,1):raise FavoritesSyncError("Invalid favorite pagination")
        page=self.make_page(data.get("medias") or [],folder,not bool(data["has_more"]),number+1,lambda r,v:v["content_id"])
        for raw,result in zip(data.get("medias") or [],page.results):
            for source,target in (("play","view_count"),("collect","collect_count"),("reply","comment_count"),("danmaku","danmaku_count")):
                value=(raw.get("cnt_info") or {}).get(source)
                if isinstance(value,int) and not isinstance(value,bool) and value>=0: result["metrics"][target]=value
        return page

class ZhihuFavoritesSource(_Source):
    platform="zhihu"; adapter=ZhihuAdapter()
    async def identity(self):
        me=await self.request(self.client.get_current_user_info); uid=(me.get("id") or me.get("uid")) if isinstance(me,dict) else None
        if not uid:
            from base.exceptions import LoginRequiredError; raise LoginRequiredError(platform="zhihu",message="知乎登录状态未能确认")
        self.me=me; return f"zhihu:{uid}"
    async def folders(self,account):
        token=self.me.get("url_token")
        if not token:raise FavoritesSyncError("Missing collection directory identity")
        out=[]; offset=0
        while True:
            data=await self.request(self.client.get_user_collections,str(token),offset,PAGE_SIZE); rows=data.get("data") if isinstance(data,dict) else None; paging=data.get("paging") if isinstance(data,dict) else None
            if not isinstance(rows,list) or not isinstance(paging,dict) or paging.get("is_end") not in (True,False):raise FavoritesSyncError("Invalid favorite folders")
            if any(not isinstance(r,dict) or not r.get("id") for r in rows): raise FavoritesSyncError("Invalid favorite folder")
            page_folders=[{"id":str(r["id"]),"name":str(r.get("title") or "默认收藏夹")} for r in rows]
            if any(item["id"] in {old["id"] for old in out} for item in page_folders): raise FavoritesSyncError("Repeated favorite folder page")
            out += page_folders
            if paging["is_end"]:return out
            if not rows:raise FavoritesSyncError("Favorite folders cannot advance")
            offset += len(rows)
    async def page(self,folder,token):
        offset=int(token or 0); data=await self.request(self.client.get_collection_items,folder["id"],offset,PAGE_SIZE)
        rows=data.get("data") if isinstance(data,dict) else None; paging=data.get("paging") if isinstance(data,dict) else None
        if not isinstance(rows,list) or not isinstance(paging,dict) or paging.get("is_end") not in (True,False):raise FavoritesSyncError("Invalid favorite pagination")
        native=[r.get("content") for r in rows if isinstance(r,dict) and isinstance(r.get("content"),dict)]
        if len(native)!=len(rows):raise FavoritesSyncError("Invalid favorite item")
        return self.make_page(native,folder,paging["is_end"],offset+len(rows),lambda r,v:f"{r.get('type')}:{v['content_id']}")

class XhsFavoritesSource(_Source):
    platform="xhs"; adapter=XhsAdapter()
    async def identity(self):
        data=await self.request(self.client.query_self); result=data.get("data",{}).get("result",{}) if isinstance(data,dict) else {}
        uid=result.get("user_id") or result.get("userId")
        if not uid:
            from base.exceptions import LoginRequiredError; raise LoginRequiredError(platform="xhs",message="小红书登录状态未能确认")
        self.user_id=str(uid); return f"xhs:{uid}"
    async def folders(self,account): return [{"id":"collected","name":"收藏笔记"}]
    async def page(self,folder,token):
        cursor="" if token is None else str(token); data=await self.request(self.client.get_collected_notes,cursor,PAGE_SIZE,self.user_id); body=data.get("data",data) if isinstance(data,dict) else {}
        rows=body.get("items") or body.get("notes"); more=body.get("has_more"); nxt=body.get("cursor")
        if not isinstance(rows,list) or more not in (True,False,0,1) or (more and not isinstance(nxt,str)):raise FavoritesSyncError("Invalid favorite pagination")
        return self.make_page(rows,folder,not bool(more),nxt,lambda r,v:v["content_id"])

class DouyinFavoritesSource(_Source):
    platform="douyin"; adapter=DouyinAdapter(); incremental_verified=False
    async def identity(self):
        # Current client exposes no verified owner endpoint. Never infer it from content/cookies.
        from base.exceptions import LoginRequiredError; raise LoginRequiredError(platform="douyin",message="抖音收藏账号身份尚未验证，暂不能全量同步")
    async def folders(self,account): return []
    async def page(self,folder,token): raise AssertionError("unreachable")

async def synchronize_favorites(source:FavoritesSource,store,mode,progress):
    if mode == "reset":
        from pathlib import Path
        from tempfile import TemporaryDirectory

        # Read a complete platform snapshot away from the live archive. A failed,
        # interrupted, or cancelled read never reaches the replacement transaction.
        with TemporaryDirectory(prefix="siye-reset-", dir=store.db_path.parent) as temp:
            staged = type(store)(Path(temp) / "staged.db")
            errors = await _synchronize_favorites(source, staged, "full", progress)
            if errors:
                return errors
            store.replace_platform_from(source.platform, staged)
            return []
    return await _synchronize_favorites(source,store,mode,progress)


async def _synchronize_favorites(source:FavoritesSource,store,mode,progress):
    account=await source.identity(); folders=await source.folders(account)
    store.observe_folders(source.platform, account, folders)
    scan=store.begin_scan(source.platform,account,folders,mode)
    saved=0; errors=[]; incremental=mode=="auto" and source.incremental_verified
    try:
        first_pages={}
        for index,folder in enumerate(folders,1):
            token=1 if source.platform=="bilibili" else None; sequence=1; run=0; reached=False; folder_read=0
            try:
                has_baseline=store.has_baseline(account,folder["id"])
                store.start_folder_diagnostic(account,folder["id"],scan,mode,has_baseline)
                checkpoint=store.checkpoint(account,folder["id"])
                if checkpoint and checkpoint["scan"]==scan and not checkpoint["complete"]:
                    # Resume only a partial scan; its baseline remains absent until
                    # a real end page is saved.  Tokens are JSON to keep cursors'
                    # string/number types across process restart.
                    try: token=json.loads(checkpoint.get("next_token") or "null")
                    except json.JSONDecodeError: token=None
                    head=await source.page(folder,1 if source.platform=="bilibili" else None)
                    if store.head_fingerprint(account,folder["id"],scan) != head.fingerprint:
                        store.restart_folder(account,folder["id"]); token=1 if source.platform=="bilibili" else None; sequence=1
                    elif token is not None: sequence=int(checkpoint["page"])+1
                while True:
                    page=await source.page(folder,token)
                    if sequence==1:first_pages[folder["id"]]=page.fingerprint
                    crossed,tail=store.baseline_run(account,folder["id"],page.identities,run); reached=reached or crossed
                    stop=incremental and has_baseline and reached
                    store.save_sync_page(source.platform,account,scan,folder["id"],folder["name"],sequence,page.fingerprint,page.results,page.complete or stop,PAGE_SIZE,page.identities,token=token,next_token=page.next_token)
                    saved += page.count; folder_read += page.count; progress(account,saved,f"正在保存 {folder['name']} · 第 {sequence} 页")
                    if page.complete or stop:
                        store.complete_folder(account,scan,folder["id"],reconcile=mode=="full")
                        store.update_folder_diagnostic(account,folder["id"],scan,pages=sequence,returned=folder_read,stop_reason="history_run" if stop else "end")
                        break
                    if page.next_token is None or page.next_token==token:raise FavoritesSyncError("Favorite page cannot advance")
                    token,sequence,run=page.next_token,sequence+1,tail
            except (FavoritesSyncError,SyncStateError):
                store.update_folder_diagnostic(account,folder["id"],scan,pages=sequence,stop_reason="error",error_code="pagination")
                errors.append(f"收藏夹 {index}：分页返回异常（第 {sequence} 页，已保存 {saved} 条）"); progress(account,saved,f"{errors[-1]}，继续下一个收藏夹")
        if errors: store.sync_status(account,"partial","；".join(errors)[:160]); return errors
        # A completed scan cannot claim a stable head if it changed while we read.
        for folder in folders:
            if folder["id"] in first_pages and (await source.page(folder,1 if source.platform=="bilibili" else None)).fingerprint != first_pages[folder["id"]]:
                raise FavoritesSyncError("Favorite head changed during sync")
        store.finish_scan(account,scan,reconcile=mode=="full"); progress(account,saved,"导入完成"); return []
    except BaseException:
        store.sync_status(account,"cancelled","同步未完成，可手动继续；未据此判断缺失"); raise
