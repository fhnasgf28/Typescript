from __future__ import annotations
from typing import Any
def success_response(data:dict[str,Any])->dict[str,Any]: return {'success':True,'data':data,'error':None}
def error_response(code:str,message:str)->dict[str,Any]: return {'success':False,'data':None,'error':{'code':code,'message':message}}
