"""Allowlisted model metadata from local CLI output; never retain raw prompts or stderr."""
import json
import os
import re
import threading
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

MODEL_ID = r'^(?:[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127})?$'
EFFORTS = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')

class AgentModelSettings(BaseModel):
    agent_model: str = Field(default='', max_length=128, pattern=MODEL_ID)
    agent_reasoning_effort: Literal['default','minimal','low','medium','high','xhigh','max','ultra'] = 'default'

def codex_model_options():
    path = Path(os.getenv('CODEX_HOME') or str(Path.home()/'.codex'))/'models_cache.json'
    try:
        payload=json.loads(path.read_text(encoding='utf-8'))
        models=payload.get('models', [])
        result=[]
        for model in models:
            slug=model.get('slug','')
            if not slug or not re.fullmatch(MODEL_ID,slug) or model.get('visibility')=='hide': continue
            efforts=[entry.get('effort') for entry in model.get('supported_reasoning_levels',[]) if isinstance(entry,dict) and entry.get('effort') in EFFORTS]
            result.append({'id':slug,'label':str(model.get('display_name') or slug)[:128], 'reasoning_efforts':efforts})
        return result[:100]
    except (OSError, ValueError, TypeError, AttributeError):
        return []

class CodexHeader:
    def __init__(self):
        self.started=False
        self.separators=0
        self.size=0
        self.done=False

    def feed(self,line):
        if self.done: return {}
        self.size += len(line)
        if self.size>8192:
            self.done=True
            return {}
        text=line.decode('utf-8',errors='replace').strip()
        if text.startswith('OpenAI Codex '): self.started=True
        if not self.started: return {}
        if re.fullmatch(r'-{3,}',text):
            self.separators+=1
            if self.separators>=2: self.done=True
            return {}
        if text in ('user','assistant','thinking','codex'):
            self.done=True
            return {}
        if self.separators!=1: return {}
        key,sep,value=text.partition(': ')
        if sep and key=='model' and value and re.fullmatch(MODEL_ID,value):
            return {'reported_model':value,'report_source':'codex_header'}
        if sep and key=='reasoning effort' and value in (*EFFORTS,'none'):
            return {'reported_reasoning_effort':value}
        return {}

def communicate_with_report(process, input_data, report):
    """Drain both pipes concurrently and report only the trusted CLI startup header."""
    header=CodexHeader()
    def drain():
        while True:
            line=process.stderr.readline(4096)
            if not line: break
            update=header.feed(line)
            if update: report(update)
    reader=threading.Thread(target=drain,daemon=True)
    reader.start()
    try:
        try:
            process.stdin.write(input_data)
            process.stdin.flush()
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
        stdout=process.stdout.read()
        process.wait()
        reader.join()
        return stdout, b''
    finally:
        process.stdout.close()
        if not reader.is_alive(): process.stderr.close()

def claude_report(events):
    models=[]
    for event in events:
        if event.get('type')=='assistant':
            value=(event.get('message') or {}).get('model')
            if isinstance(value,str) and value and re.fullmatch(MODEL_ID,value) and value not in models:
                models.append(value)
    return {'reported_model':', '.join(models),'report_source':'claude_message'} if models else {}


def read_model_settings(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def local_model_catalogs(claude_command=None, antigravity_command=None):
    """Read only model identifiers/names; never expose credentials or settings blobs."""
    import subprocess
    catalogs = {'codex': codex_model_options(), 'claude': [], 'qwen': [], 'antigravity': [], 'custom': []}
    def add(provider, value, label=None):
        if isinstance(value,str) and value and re.fullmatch(MODEL_ID,value) and not any(m['id']==value for m in catalogs[provider]):
            catalogs[provider].append({'id':value,'label':str(label or value)[:128]})
    claude = read_model_settings(Path(os.getenv('CLAUDE_CONFIG_DIR') or str(Path.home()/'.claude'))/'settings.json')
    add('claude',claude.get('model'))
    for value in claude.get('availableModels',[]) if isinstance(claude.get('availableModels'),list) else []:
        add('claude',value)
    for key in ('ANTHROPIC_MODEL','ANTHROPIC_DEFAULT_OPUS_MODEL','ANTHROPIC_DEFAULT_SONNET_MODEL','ANTHROPIC_DEFAULT_HAIKU_MODEL'):
        add('claude',os.getenv(key))
        env=claude.get('env',{})
        if isinstance(env,dict): add('claude',env.get(key))
    qwen = read_model_settings(Path.home()/'.qwen'/'settings.json')
    configured=qwen.get('model',{})
    add('qwen',configured.get('name') if isinstance(configured,dict) else configured)
    providers=qwen.get('modelProviders',{})
    if isinstance(providers,dict):
        for models in providers.values():
            if isinstance(models,list):
                for model in models:
                    if isinstance(model,dict): add('qwen',model.get('id'),model.get('name'))
    def run(args):
        try:
            result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                timeout=8,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            return result.stdout.decode('utf-8',errors='replace')[:100000] if result.returncode==0 else ''
        except (OSError,subprocess.SubprocessError): return ''
    if claude_command:
        for model in claude_model_options(claude_command):
            add('claude',model['id'],model['label'])
    if antigravity_command:
        # `models` is the documented catalog command, not a prompt.
        text=run(antigravity_command+['models'])
        for line in text.splitlines():
            match=re.match(r'^\s*(?:[│|*]\s*)?((?:gemini|claude|gpt)-[A-Za-z0-9._:-]+)(?:\s|[│|]|$)',line)
            if match: add('antigravity',match.group(1))
    return {key:values[:100] for key,values in catalogs.items()}


def structured_model_report(events, provider):
    """Only protocol metadata counts, never model names mentioned in prose."""
    models=[]
    def add(value):
        if isinstance(value,str) and value and re.fullmatch(MODEL_ID,value) and value not in models: models.append(value)
    for event in events:
        if not isinstance(event,dict): continue
        if provider in ('claude','qwen'):
            if event.get('type')=='system' and event.get('subtype') in ('init','session_start'): add(event.get('model'))
            if event.get('type')=='assistant' and isinstance(event.get('message'),dict): add(event['message'].get('model'))
        elif provider=='antigravity' and event.get('event')=='init' and isinstance(event.get('init'),dict):
            add(event['init'].get('model'))
    return {'reported_model':', '.join(models),'report_source':provider+'_metadata'} if models else {}


def claude_model_options(command):
    """SDK initialization returns the model catalog without sending a user prompt."""
    import subprocess
    request={'type':'control_request','request_id':'model-discovery','request':{'subtype':'initialize'}}
    args=command+['-p','--input-format','stream-json','--output-format','stream-json','--verbose',
        '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
        '--settings','{"disableAllHooks":true}','--no-session-persistence']
    try:
        result=subprocess.run(args,input=(json.dumps(request)+'\n').encode(),stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode: return []
        options=[]
        for line in result.stdout.decode('utf-8',errors='replace').splitlines():
            try: event=json.loads(line)
            except ValueError: continue
            response=event.get('response',{}) if isinstance(event,dict) else {}
            if event.get('type')!='control_response' or response.get('request_id')!='model-discovery' or response.get('subtype')!='success': continue
            payload=response.get('response',{})
            for model in payload.get('models',[]) if isinstance(payload,dict) else []:
                if not isinstance(model,dict): continue
                identifier=model.get('resolvedModel') or model.get('value')
                if isinstance(identifier,str) and identifier not in ('default','opus','sonnet','haiku') and re.fullmatch(MODEL_ID,identifier) and not any(m['id']==identifier for m in options):
                    # resolvedModel is the concrete ID behind aliases such as opus/sonnet.
                    options.append({'id':identifier,'label':identifier})
        return options[:100]
    except (OSError,subprocess.SubprocessError,AttributeError,TypeError): return []
