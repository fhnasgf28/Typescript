from __future__ import annotations
import json, os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
HOME_ALLOWLIST_PREFIX=Path('/home/fhnasgf').resolve(); DEFAULT_BASE_DIR=HOME_ALLOWLIST_PREFIX/'mcp-transfer'
@dataclass(frozen=True)
class TransferSettings:
    server_name:str; base_dir:Path; max_file_mb:int; public_url:str; web_admin_password:str; session_secret:str
    @property
    def inbox_dir(self)->Path: return self.base_dir/'inbox'
    @property
    def metadata_dir(self)->Path: return self.base_dir/'metadata'
    @property
    def config_dir(self)->Path: return self.base_dir/'config'
    @property
    def logs_dir(self)->Path: return self.base_dir/'logs'
@dataclass(frozen=True)
class Destination: name:str; url:str; token_env:str
@dataclass(frozen=True)
class AllowedPeer: name:str; token_hash:str; enabled:bool
def _env_value(env:Mapping[str,str], key:str, default:str|None=None)->str:
    v=env.get(key, default)
    if v is None or v=='': raise ValueError(f'missing required environment variable: {key}')
    return v
def _ensure_under_home(path:Path)->Path:
    resolved=path.expanduser().resolve()
    try: resolved.relative_to(HOME_ALLOWLIST_PREFIX)
    except ValueError as exc: raise ValueError('base dir must be under /home/fhnasgf') from exc
    return resolved
def _load_base_dir(env:Mapping[str,str])->Path:
    raw=env.get('MCP_TRANSFER_BASE_DIR')
    if raw is None: return DEFAULT_BASE_DIR
    raw=raw.strip()
    if not raw or not Path(raw).is_absolute(): raise ValueError('MCP_TRANSFER_BASE_DIR must be a non-empty absolute path under /home/fhnasgf')
    return _ensure_under_home(Path(raw))
def load_settings(env:Mapping[str,str]|None=None)->TransferSettings:
    src=os.environ if env is None else env
    max_mb=int(src.get('MCP_TRANSFER_MAX_FILE_MB','50'))
    if max_mb<1 or max_mb>50: raise ValueError('max file size must be between 1 and 50 MB')
    return TransferSettings(_env_value(src,'MCP_TRANSFER_SERVER_NAME'),_load_base_dir(src),max_mb,_env_value(src,'MCP_TRANSFER_PUBLIC_URL'),_env_value(src,'MCP_TRANSFER_WEB_ADMIN_PASSWORD'),_env_value(src,'MCP_TRANSFER_SESSION_SECRET'))
def _obj(path:Path)->dict[str,object]:
    try: payload=json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc: raise ValueError(f'{path}: invalid JSON') from exc
    if not isinstance(payload,dict): raise ValueError(f'{path}: expected a top-level JSON object')
    return payload
def _items(path:Path,payload:dict[str,object],key:str,required:tuple[str,...])->list[dict[str,object]]:
    items=payload.get(key)
    if not isinstance(items,list): raise ValueError(f"{path}: expected '{key}' to be a JSON array")
    out=[]
    for i,item in enumerate(items):
        if not isinstance(item,dict): raise ValueError(f'{path}: {key}[{i}] must be a JSON object')
        miss=[k for k in required if k not in item]
        if miss: raise ValueError(f"{path}: {key}[{i}] missing required keys: {', '.join(miss)}")
        out.append(item)
    return out
def load_destinations(config_path:Path)->list[Destination]:
    if not config_path.exists(): return []
    return [Destination(str(x['name']),str(x['url']).rstrip('/'),str(x['tokenEnv'])) for x in _items(config_path,_obj(config_path),'destinations',('name','url','tokenEnv'))]
def load_allowed_peers(config_path:Path)->list[AllowedPeer]:
    if not config_path.exists(): return []
    return [AllowedPeer(str(x['name']),str(x['tokenHash']),bool(x['enabled'])) for x in _items(config_path,_obj(config_path),'allowedPeers',('name','tokenHash','enabled'))]
