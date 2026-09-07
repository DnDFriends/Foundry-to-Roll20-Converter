#!/usr/bin/env python3
"""Foundry dnd5e Actor -> Roll20 2014 / VTTES schema 3. Python 3.10+.

No network access, external packages, eval, or execution of source macros.
Run without arguments for the file-picker app, or --help for the CLI.
"""
import argparse
import ast
import copy
import html
from html.parser import HTMLParser
import json
import math
import operator
from pathlib import Path
import re
import secrets
import sys
from collections import Counter, defaultdict

VERSION = "1.3.0"
ABILITIES = dict(zip(('str','dex','con','int','wis','cha'),
                     ('strength','dexterity','constitution','intelligence','wisdom','charisma')))
SKILLS = {
    'acr': ('acrobatics','dex'), 'ani': ('animal_handling','wis'), 'arc': ('arcana','int'),
    'ath': ('athletics','str'), 'dec': ('deception','cha'), 'his': ('history','int'),
    'ins': ('insight','wis'), 'itm': ('intimidation','cha'), 'inv': ('investigation','int'),
    'med': ('medicine','wis'), 'nat': ('nature','int'), 'prc': ('perception','wis'),
    'prf': ('performance','cha'), 'per': ('persuasion','cha'), 'rel': ('religion','int'),
    'slt': ('sleight_of_hand','dex'), 'ste': ('stealth','dex'), 'sur': ('survival','wis')}
SCHOOLS = dict(zip(('abj','con','div','enc','evo','ill','nec','trs'),
                   ('abjuration','conjuration','divination','enchantment','evocation','illusion','necromancy','transmutation')))
PHYSICAL = {'weapon','equipment','consumable','container','backpack','loot','tool'}
ROLLABLE = {'attack','save','damage','heal'}
SLOTS = [[],[2],[3],[4,2],[4,3],[4,3,2],[4,3,3],[4,3,3,1],[4,3,3,2],
         [4,3,3,3,1],[4,3,3,3,2],[4,3,3,3,2,1],[4,3,3,3,2,1],
         [4,3,3,3,2,1,1],[4,3,3,3,2,1,1],[4,3,3,3,2,1,1,1],
         [4,3,3,3,2,1,1,1],[4,3,3,3,2,1,1,1,1],[4,3,3,3,3,1,1,1,1],
         [4,3,3,3,3,2,1,1,1],[4,3,3,3,3,2,2,1,1]]


def uid():
    # Exactly 20 characters; no underscore (repeating-row delimiter).
    return '-' + ''.join(secrets.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-') for _ in range(19))


def values(obj):
    return list(obj.values()) if isinstance(obj, dict) else obj if isinstance(obj, list) else []


def get(obj, path, default=None):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return default if obj is None else obj


def put(obj, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        if not isinstance(obj.get(key), dict):
            obj[key] = {}
        obj = obj[key]
    obj[keys[-1]] = value


def number(value, default=0):
    try:
        n = float(value)
        return int(n) if n.is_integer() else n
    except (ValueError, TypeError, OverflowError):
        return default


def slug(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'):
            self.skip += 1
        if not self.skip:
            if tag in ('p','div','br','tr','h1','h2','h3','h4','hr'):
                self.parts.append('\n')
            elif tag == 'li':
                self.parts.append('\n• ')
            elif tag in ('td','th'):
                self.parts.append(' | ')

    def handle_endtag(self, tag):
        if tag in ('script','style'):
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in ('p','div','li','tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean(text):
    parser = PlainText()
    parser.feed(str(text or ''))
    result = ''.join(parser.parts)
    # Balanced brackets are necessary for tags such as Cube [Area of Effect].
    pattern = re.compile(r'(?:@[\w]+|&Reference)\[')
    pos = 0
    out = []
    while True:
        match = pattern.search(result, pos)
        if not match:
            out.append(result[pos:]); break
        out.append(result[pos:match.start()])
        start = end = match.end()
        depth = 1
        while end < len(result) and depth:
            depth += (result[end] == '[') - (result[end] == ']')
            end += 1
        if depth:
            out.append(result[match.start():]); break
        parts = result[start:end-1].split('|')
        label = parts[2] if len(parts) > 2 and parts[2] else parts[0]
        if end < len(result) and result[end] == '{':
            stop = result.find('}', end)
            if stop != -1:
                label = result[end+1:stop]; end = stop+1
        if match.group().startswith('&Reference'):
            label = label.split('=',1)[-1]
        out.append(label)
        pos = end
    result = ''.join(out)
    result = re.sub(r'\[\[/([\w]+)\s+(.*?)\]\]', lambda m: m[2] if m[1] in ('damage','heal','r') else m[1]+' '+m[2], result)
    result = re.sub(r'\[\[(.*?)\]\]', r'\1', result)
    result = re.sub(r'[ \t]+',' ',result)
    return re.sub(r'\n\s*\n+', '\n\n', result).strip()


def arithmetic(expr):
    """Small, bounded arithmetic grammar. Never execute Python/Foundry scripts."""
    if len(str(expr)) > 1024:
        raise ValueError('Formula too long')
    node = ast.parse(str(expr), mode='eval')
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
    funcs = {'floor': math.floor, 'ceil': math.ceil, 'round': round, 'min': min, 'max': max, 'abs': abs}
    def run(n, depth=0):
        if depth > 30:
            raise ValueError('Formula too deeply nested')
        if isinstance(n, ast.Expression):
            return run(n.body, depth+1)
        if isinstance(n, ast.Constant) and type(n.value) in (float,int):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            v = ops[type(n.op)](run(n.left,depth+1),run(n.right,depth+1))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            v = run(n.operand,depth+1) * (-1 if isinstance(n.op,ast.USub) else 1)
        elif isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in funcs and not n.keywords:
            v = funcs[n.func.id](*(run(a,depth+1) for a in n.args))
        else:
            raise ValueError('Unsupported arithmetic')
        if not math.isfinite(v) or abs(v) > 1e9:
            raise ValueError('Formula value out of range')
        return v
    return run(node)


class Converter:
    def __init__(self, actor, spell_ability=None, overrides=None, review=None):
        if not isinstance(actor,dict) or not isinstance(actor.get('system'),dict) or not isinstance(actor.get('items'),list):
            raise ValueError('Choose a Foundry Actor export with system and items, not a Roll20 or single Item export.')
        if actor.get('type') != 'character':
            raise ValueError('This version supports player-character Actors. NPC sheets require a separate mapping.')
        system_id = get(actor,'_stats.systemId',get(actor,'_stats.exportSource.systemId','dnd5e'))
        if system_id != 'dnd5e':
            raise ValueError('This converter supports the Foundry dnd5e system only.')
        self.actor = copy.deepcopy(actor)
        self.system = self.actor['system']
        self.items = self.actor['items']
        self.overrides = overrides or {}
        self.review = review or {}
        if not isinstance(self.overrides,dict):
            raise ValueError('Overrides must be a JSON object.')
        for key,value in self.overrides.items():
            if key not in set(ABILITIES)|{'ac','hp_max','hp_current','speed','initiative'}:
                raise ValueError('Unknown override: '+key)
            if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e6 or (key!='initiative' and value<0):
                raise ValueError('Invalid numeric override: '+key)
        self.warnings = []
        self.notes = []
        self.rows = defaultdict(list)
        self.attrs = {}
        self.counts = Counter()
        self.pending_resources = []
        self.attack_row_keys = {}
        self.classes = [i for i in self.items if i['type']=='class']
        self.level = int(sum(number(get(i,'system.levels')) for i in self.classes) or get(self.system,'details.level',1))
        self.pb = number(get(self.system,'attributes.prof'), 2+(max(1,self.level)-1)//4)
        self.scales = {}
        for item in self.items:
            s = item['system']
            class_id = s.get('identifier') or slug(item['name'])
            level = number(s.get('levels'),self.level)
            for advance in values(s.get('advancement')):
                if advance.get('type') != 'ScaleValue':
                    continue
                cfg = advance.get('configuration',{})
                key = cfg.get('identifier') or slug(advance.get('title',''))
                eligible = [(int(k),v) for k,v in cfg.get('scale',{}).items() if k.isdigit() and int(k)<=level]
                if eligible:
                    v = max(eligible,key=lambda x:x[0])[1]
                    if 'value' in v:
                        scale_value=v['value']
                    elif v.get('faces'):
                        scale_value=f"{v.get('number',1) or 1}d{v['faces']}"
                    else:
                        scale_value=0
                    self.scales[f'scale.{class_id}.{key}'] = scale_value
        self.apply_effects()
        self.scores = {k:number(get(self.system,f'abilities.{k}.value'),10) for k in ABILITIES}
        for k in ABILITIES:
            if k in self.overrides:
                self.scores[k] = self.overrides[k]
        self.mods = {k:math.floor((v-10)/2) for k,v in self.scores.items()}
        detected = spell_ability or get(self.system,'attributes.spellcasting','')
        if detected not in ABILITIES:
            candidates = {get(c,'system.spellcasting.ability') for c in self.classes} - {None,''}
            if len(candidates)==1:
                detected = next(iter(candidates))
        if detected not in ABILITIES:
            candidates = {get(i,'system.ability') for i in self.items if i['type']=='spell'} & ABILITIES.keys()
            if len(candidates)==1:
                detected = next(iter(candidates))
        if detected not in ABILITIES and any(i['type']=='spell' for i in self.items):
            raise ValueError('Spellcasting ability is ambiguous or missing. Select an ability in the app or use --spell-ability. Scores: '+str(self.scores))
        self.spell_ability = detected if detected in ABILITIES else None
        self.spellmod = self.mods.get(self.spell_ability,0)
        self.spell_dc_bonus = self.numeric(get(self.system,'bonuses.spell.dc',''),label='Spell DC bonus')
        self.dc = 8+self.pb+self.spellmod+self.spell_dc_bonus
        self.character_id = uid()

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def context(self, ability=None):
        ctx = {'prof':self.pb,'attributes.prof':self.pb,'level':self.level,'details.level':self.level,
               'mod':getattr(self,'mods',{}).get(ability or getattr(self,'spell_ability',None),0)}
        for k in ABILITIES:
            val = getattr(self,'scores',{}).get(k,number(get(self.system,f'abilities.{k}.value'),10))
            ctx[f'abilities.{k}.value']=val
            ctx[f'abilities.{k}.mod']=math.floor((val-10)/2)
        for c in self.classes:
            ctx[f"classes.{get(c,'system.identifier',slug(c['name']))}.levels"] = get(c,'system.levels',0)
        ctx.update(self.scales)
        return ctx

    def substitute(self, expr, ability=None):
        ctx = self.context(ability)
        def scale_multiple(m):
            multiplier=int(m[1]);value=ctx.get(m[2])
            dice=re.fullmatch(r'(\d*)d(\d+)',str(value or ''),re.I)
            return f'{multiplier*int(dice[1] or 1)}d{dice[2]}' if dice else m[0]
        expr=re.sub(r'(\d+)@(scale\.[\w.-]+)',scale_multiple,str(expr or '0'))
        def replace(m):
            key=m[1]
            if key in ctx:
                return str(ctx[key])
            val=get(self.system,key,None)
            if isinstance(val,(int,float)):
                return str(val)
            raise ValueError('Unknown reference @'+key)
        return re.sub(r'@([\w.-]+)',replace,expr)

    def numeric(self, expr, ability=None, label='Formula', default=0):
        if expr in ('',None):
            return default
        try:
            return number(arithmetic(self.substitute(expr,ability)))
        except (ValueError,SyntaxError,TypeError,ZeroDivisionError,OverflowError) as e:
            self.warn(f'{label}: could not evaluate {expr!r} ({e}); used {default}. Review manually.')
            return default

    def formula(self, expr, ability=None, label='Damage'):
        try:
            result = self.substitute(expr,ability)
            # Foundry scale dice may be stored as "d12" and concatenated as
            # "2@scale.monk.die". Roll20 requires an explicit die count.
            result = re.sub(r'(?<![\w\d])d(\d+)', r'1d\1', result, flags=re.I)
            # Reject executable macros / unsupported expressions; permit standard dice and arithmetic.
            safe = re.sub(r'\b\d*d\d+(?:k[hl]\d+)?\b','1',result,flags=re.I)
            arithmetic(safe)
            return result
        except (ValueError,SyntaxError,TypeError,ZeroDivisionError,OverflowError) as e:
            self.warn(f'{label}: unsupported roll formula {expr!r} ({e}); roll is omitted, original text retained in the report.')
            return ''

    def change(self, target, change, source):
        key=change.get('key','')
        if not key.startswith('system.'):
            self.warn(f'{source}: effect change {key!r} needs manual handling.'); return
        mode=change.get('type',{0:'custom',1:'multiply',2:'add',3:'downgrade',4:'upgrade',5:'override'}.get(change.get('mode')))
        raw=change.get('value')
        old=get(target,key,None)
        if isinstance(old,list):
            val=str(raw)
            if mode=='add':
                updated=[v for v in old if v!=val[1:]] if val.startswith('-') else list(dict.fromkeys(old+[raw]))
            elif mode=='override':updated=raw if isinstance(raw,list) else [raw]
            else:self.warn(f'{source}: unsupported list effect {key} / {mode}.');return
        elif mode=='override':
            updated=raw
        elif isinstance(old,str) and number(old,None) is None and not re.fullmatch(r'[+\-]?\d+(\.\d+)?',str(raw or '')):
            if mode=='add':updated=old+str(raw)
            else:self.warn(f'{source}: unsupported text effect {key} / {mode}.');return
        else:
            try:val=arithmetic(self.substitute(raw))
            except (ValueError,SyntaxError,TypeError,ZeroDivisionError):
                self.warn(f'{source}: unsupported effect formula {key} = {raw!r}.');return
            old=number(old,0)
            if mode=='add':updated=old+val
            elif mode=='subtract':updated=old-val
            elif mode=='multiply':updated=old*val
            elif mode=='upgrade':updated=max(old,val)
            elif mode=='downgrade':updated=min(old,val)
            else:self.warn(f'{source}: unsupported effect mode {mode} at {key}.');return
        put(target,key,updated)

    def apply_effects(self):
        changes=[]
        def collect(effect, source):
            if effect.get('disabled') or get(effect,'duration.expired',False):return
            for ch in get(effect,'system.changes',effect.get('changes',[])):
                changes.append((number(ch.get('priority'),number(ch.get('mode'),2)*10),ch,source))
            if get(effect,'flags.dae.enableCondition') or get(effect,'flags.dae.disableCondition'):
                self.warn(f'{source}: conditional effect state requires review; used the exported enabled/disabled state.')
        for e in self.actor.get('effects',[]):collect(e,e.get('name','Actor effect'))
        for i in self.items:
            s=i['system']
            for e in i.get('effects',[]):
                if e.get('disabled'):continue
                if e.get('type')=='enchantment':
                    # Non-transferring enchantments without an origin are templates
                    # the spell applies to a future target, not effects on this item.
                    if not e.get('transfer') and not e.get('origin') and not get(e,'flags.core.originText'):
                        continue
                    for ch in get(e,'system.changes',e.get('changes',[])):
                        match=re.match(r'activities\[([^]]+)\]\.(.*)',ch.get('key',''))
                        if match:
                            for a in values(s.get('activities')):
                                if a.get('type')==match[1]:
                                    tmp={'system':a}; cc=dict(ch,key='system.'+match[2]);self.change(tmp,cc,i['name'])
                        else:self.change(i,ch,i['name'])
                    continue
                if not e.get('transfer') or i['type']=='spell':continue
                if i['type'] in PHYSICAL and (not s.get('equipped') or (s.get('attunement') in ('required',1) and not s.get('attuned'))):continue
                collect(e,i['name']+' / '+e.get('name','Effect'))
        for _,ch,source in sorted(changes,key=lambda x:x[0]):
            self.change(self.actor,ch,source)
            self.notes.append(f'Applied {source}: {ch.get("key")} {ch.get("type",ch.get("mode"))} {ch.get("value")}')

    def add(self,name,value='',maximum=''):
        self.attrs[name]={'name':name,'current':str(value if value is not None else ''),'max':str(maximum if maximum is not None else ''),'id':self.attrs.get(name,{}).get('id',uid())}

    def row(self,section,fields,rowid=None):
        rowid=rowid or uid()
        if rowid not in self.rows[section]:self.rows[section].append(rowid)
        for k,v in fields.items():self.add(f'repeating_{section}_{rowid}_{k}',v)
        return rowid

    def hp_max(self):
        explicit=get(self.system,'attributes.hp.max',None)
        if explicit is not None:return self.numeric(explicit,label='Maximum HP')
        total=0
        for c in self.classes:
            s=c['system'];hd=int(str(get(s,'hd.denomination',s.get('hitDice','d8'))).lstrip('d'))
            advances=[a for a in values(s.get('advancement')) if a.get('type')=='HitPoints']
            levels=int(number(s.get('levels')))
            hp=advances[0].get('value',{}) if advances else {}
            if not hp:
                self.warn(f'{c["name"]}: missing HP advancement; assumed maximum at first character level and averages thereafter.')
            for lv in range(1,levels+1):
                v=hp.get(str(lv),'max' if lv==1 and c==self.classes[0] else 'avg')
                base=hd if v=='max' else hd//2+1 if v=='avg' else self.numeric(v,label=c['name']+' HP')
                total+=max(1,base+self.mods['con'])
        if not self.classes:
            self.warn('Maximum HP could not be calculated; using current HP as provisional maximum.')
            total=number(get(self.system,'attributes.hp.value'))
        total+=self.numeric(get(self.system,'attributes.hp.bonuses.level'),label='HP per-level bonus')*self.level
        total+=self.numeric(get(self.system,'attributes.hp.bonuses.overall'),label='HP bonus')
        total+=self.numeric(get(self.system,'attributes.hp.tempmax'),label='Temporary maximum HP')
        return max(1,total)

    def armor_class(self):
        s=self.system;ac=get(s,'attributes.ac',{});dex=self.mods['dex']
        explicit=ac.get('value',ac.get('flat'))
        if explicit is not None:return number(explicit)
        calc=ac.get('calc','default')
        if calc=='custom':return self.numeric(ac.get('formula'),label='Custom AC',default=10+dex)
        bases=[10+dex];shields=[]
        for i in self.items:
            a=get(i,'system.armor',{});kind=get(i,'system.type.value',a.get('type'))
            if not get(i,'system.equipped',False) or a.get('value') is None:continue
            base=self.numeric(a['value'],label=i['name']+' armor')+self.numeric(a.get('magicalBonus'),label=i['name']+' AC magic')
            if kind=='shield':shields.append(base)
            elif kind in ('light','medium','heavy'):
                cap=a.get('dex',0 if kind=='heavy' else 2 if kind=='medium' else None)
                bases.append(base+(min(dex,number(cap)) if cap is not None else dex))
        if calc in ('unarmoredMonk','unarmoredBarb','mage'):
            bases=[10+dex+(self.mods['wis'] if calc=='unarmoredMonk' else self.mods['con'] if calc=='unarmoredBarb' else 3)]
        elif calc not in ('default','flat','natural'):
            self.warn(f'AC calculation {calc!r} not fully supported; verify imported AC.')
        return max(bases)+max(shields,default=0)+self.numeric(ac.get('bonus'),label='AC bonus')

    def simple_roll(self,name,bonus,mode=0):
        dice='{1d20,1d20}kh1' if mode>0 else '{1d20,1d20}kl1' if mode<0 else '1d20'
        return '@{wtype}&{template:simple} {{rname='+name+'}} {{mod=@{'+bonus+'}}} {{r1=[['+dice+'+@{'+bonus+'}]]}} {{normal=1}} @{charname_output}'

    def core(self):
        s=self.system
        defaults={'l1mancer_status':'completed','sheet_version':1,'npc':0,'level':self.level,'base_level':self.level,
                  'version':'4.21',
                  'pb':self.pb,'pb_type':'level','d20':'1d20','rtype':'{{normal=1}} {{r2=[[0d20','wtype':'',
                  'dtype':'full','pbd_safe':'','jack':0,'jack_of_all_trades':0,'globalmagicmod':0,
                  'global_skill_mod':0,'global_save_mod':0,'global_attack_mod':0,'global_damage_mod_roll':0,
                  'global_damage_mod_crit':0,'global_damage_mod_type':'','global_damage_mod':0,
                  'charname_output':'{{charname=@{character_name}}}','character_name':self.actor['name'],
                  'init_tiebreaker':'@{dexterity}/100','custom_ac_flag':2,'simpletraits':'complex','simpleinventory':'complex','simpleproficencies':'complex',
                  'spellcasting_ability':f'@{{{ABILITIES[self.spell_ability]}_mod}}+' if self.spell_ability else '0*',
                  'spell_dc_mod':self.spell_dc_bonus,'spell_save_dc':self.dc,'spell_attack_mod':0,
                  'spell_attack_bonus':self.pb+self.spellmod,'inspiration': 'on' if get(s,'attributes.inspiration') else 0}
        for k,v in defaults.items():self.add(k,v)
        for short,long in ABILITIES.items():
            self.add(long,self.scores[short]);self.add(long+'_base',self.scores[short]);self.add(long+'_mod',self.mods[short])
            ab=get(s,'abilities.'+short,{})
            prof=number(ab.get('proficient'))
            extra=self.numeric(get(ab,'bonuses.save'),short,long+' save bonus')+self.numeric(get(s,'bonuses.abilities.save'),short,'Global save bonus')
            self.add(long+'_save_prof','(@{pb})' if prof>=1 else 0)
            self.add(long+'_save_mod',extra)
            self.add(long+'_save_bonus',self.mods[short]+math.floor(prof*self.pb)+extra)
            self.add(long+'_save_roll',self.simple_roll(long.title()+' Save',long+'_save_bonus',number(get(ab,'save.roll.mode'))))
        for short,(long,base_ability) in SKILLS.items():
            sk=get(s,'skills.'+short,{})
            ability=sk.get('ability',base_ability)
            if ability not in ABILITIES:ability=base_ability
            prof=number(sk.get('value'))
            extra=sum(self.numeric(v,ability,long+' bonus') for v in [get(sk,'bonuses.check'),get(s,'bonuses.abilities.skill'),get(s,'bonuses.abilities.check')])
            flat=extra+self.mods[ability]-self.mods[base_ability]
            if prof==0.5:flat+=math.floor(self.pb/2)
            self.add(long+'_prof',f'(@{{pb}}*@{{{long}_type}})' if prof>=1 else 0)
            self.add(long+'_type',2 if prof>=2 else 1)
            self.add(long+'_flat',flat)
            self.add(long+'_bonus',self.mods[base_ability]+flat+(self.pb*(2 if prof>=2 else 1) if prof>=1 else 0))
            mode=number(get(sk,'roll.mode'))
            self.add(long+'_roll',self.simple_roll(long.replace('_',' ').title(),long+'_bonus',mode))
            if mode:self.warn(f'{long.replace("_"," ").title()}: exported roll has {"advantage" if mode>0 else "disadvantage"}; initial roll honors it. Roll20 recalculation may reset this, so check conditional circumstances manually.')
            if long=='perception':self.add('passive_wisdom',10+number(self.attrs[long+'_bonus']['current'])+self.numeric(get(sk,'bonuses.passive'))+(5 if mode>0 else -5 if mode<0 else 0))
        hp=number(self.overrides.get('hp_max',self.hp_max()));current=number(self.overrides.get('hp_current',number(get(s,'attributes.hp.value'),hp)))
        if current>hp:
            self.warn(f'Stored current HP {current} exceeds calculated maximum {hp}; current HP capped at {hp}.')
            current=hp
        self.add('hp',max(0,current),hp);self.add('hp_temp',get(s,'attributes.hp.temp',0))
        ac=self.overrides.get('ac',self.armor_class());self.add('ac',ac)
        self.notes.append('AC uses manual tracking to preserve the computed Foundry total; update AC after changing equipment.')
        movement=get(s,'attributes.movement',{})
        race=next((i for i in self.items if i['type']=='race'),{})
        speed=movement.get('walk',get(race,'system.movement.walk',0))
        self.add('speed',self.overrides.get('speed',number(speed)))
        initiative_ability=get(s,'attributes.init.ability','dex') or 'dex'
        init_bonus=self.numeric(get(s,'attributes.init.bonus'),initiative_ability,'Initiative bonus')
        init=self.overrides.get('initiative',self.mods.get(initiative_ability,self.mods['dex'])+init_bonus)
        self.add('initiative_bonus',round(init+self.scores['dex']/100,2));self.add('initmod',init-self.mods['dex']);self.add('initdex',self.mods['dex'])
        self.notes.append(f'Initiative is {init:+g}, plus Dexterity tiebreaker {self.scores["dex"]/100:g} in Roll20.')
        self.add('initiative_style','@{d20}')
        self.add('race',race.get('name',''))
        subclasses={get(i,'system.classIdentifier',''):i['name'] for i in self.items if i['type']=='subclass'}
        self.add('background',next((i['name'] for i in self.items if i['type']=='background'),''))
        self.add('class_display',', '.join(f"{c['name']} {get(c,'system.levels',0)}" for c in self.classes))
        for idx,c in enumerate(self.classes):
            class_key=get(c,'system.identifier',slug(c['name']))
            subclass_name=subclasses.get(class_key,'')
            if idx==0:
                self.add('class',c['name']);self.add('base_level',get(c,'system.levels',0));self.add('subclass',subclass_name)
            elif idx<=3:
                self.add(f'multiclass{idx}_flag',1);self.add(f'multiclass{idx}',c['name']);self.add(f'multiclass{idx}_lvl',get(c,'system.levels',0));self.add(f'multiclass{idx}_subclass',subclass_name)
            else:self.warn('More than four classes: class display retained; class automation needs manual setup.')
        hd=defaultdict(lambda:[0,0])
        for c in self.classes:
            die=str(get(c,'system.hd.denomination',get(c,'system.hitDice','d8'))).lstrip('d')
            n=number(get(c,'system.levels'));spent=number(get(c,'system.hd.spent',get(c,'system.hitDiceUsed',0)))
            hd[die][0]+=max(0,n-spent);hd[die][1]+=n
        self.add('hitdice',sum(x[0] for x in hd.values()),sum(x[1] for x in hd.values()))
        self.add('hitdietype',next(iter(hd),'8'));self.add('hitdie_final','@{hitdietype}')
        if len(hd)>1:self.warn('Mixed hit dice: total is imported; per-die pools are also in Resources. Select the appropriate die before rolling.')
        for die,(remaining,total) in hd.items():
            self.add('hitdice_d'+die,remaining,total)
        for key in ('alignment','age','height','weight','eyes','hair','skin','faith','gender'):
            self.add(key,get(s,'details.'+key,''))
        self.add('experience',get(s,'details.xp.value',0))
        for field,source in [('personality_traits','trait'),('ideals','ideal'),('bonds','bond'),('flaws','flaw')]:self.add(field,get(s,'details.'+source,''))
        for coin in ('cp','sp','ep','gp','pp'):self.add(coin,get(s,'currency.'+coin,0))
        aliases={'ALL':'All Languages','sim':'Simple Weapons','mar':'Martial Weapons','lgt':'Light Armor','med':'Medium Armor','hvy':'Heavy Armor','shl':'Shields','sign':'Common Sign Language'}
        for label,path in [('Languages','languages'),('Armor Proficiencies','armorProf'),('Weapon Proficiencies','weaponProf')]:
            trait=get(s,'traits.'+path,{})
            names=', '.join(aliases.get(str(v),str(v).replace('_',' ').title()) for v in trait.get('value',[]))
            if trait.get('custom'):names+=('; ' if names else '')+trait['custom']
            if names:self.row('traits',{'name':label,'description':names,'source':'Proficiency','options-flag':0,'display_flag':'on'})
        defense_fields=[('Damage Immunities','di'),('Damage Resistances','dr'),('Condition Immunities','ci'),('Damage Vulnerabilities','dv')]
        designated=self.review.get('defenses',{})
        for label,path in defense_fields:
            trait=get(s,'traits.'+path,{})
            default=', '.join(str(v).replace('_',' ').title() for v in trait.get('value',[]))
            if trait.get('custom'):default+=('; ' if default else '')+trait['custom']
            text=designated.get(path,default)
            if text:self.row('traits',{'name':label,'description':text,'source':'Defense','options-flag':0,'display_flag':'on'})
        senses=get(s,'attributes.senses',{})
        ranges=dict(senses.get('ranges',{}));ranges.update({k:v for k,v in senses.items() if k in ('darkvision','blindsight','tremorsense','truesight')})
        sense_text=', '.join(f'{k.title()} {v} {senses.get("units") or "ft"}' for k,v in ranges.items() if v)
        if senses.get('special'):sense_text+=('; ' if sense_text else '')+senses['special']
        sense_text=designated.get('senses',sense_text)
        if sense_text:self.row('traits',{'name':'Special Senses','description':sense_text,'source':'Sense','options-flag':0,'display_flag':'on'})
        tool_names={'water':"Water Vehicles",'navg':"Navigator's Tools",'herb':'Herbalism Kit','thief':"Thieves' Tools",'dice':'Dice Set','smith':"Smith's Tools",'mason':"Mason's Tools",'leatherworker':"Leatherworker's Tools",'cobbler':"Cobbler's Tools",'carpenter':"Carpenter's Tools"}
        for key,t in get(s,'tools',{}).items():
            ability=t.get('ability','int');prof=number(t.get('value'))
            bonus=self.mods.get(ability,0)+math.floor(self.pb*prof)+self.numeric(get(t,'bonuses.check'),ability,'Tool bonus')
            display=tool_names.get(key,key.replace('_',' ').title())
            self.row('tool',{'toolname':display,'toolattr_base':f'@{{{ABILITIES.get(ability,"intelligence")}_mod}}',
                             'toolbonus_base':'@{pb}' if prof else '0','toolbonus':bonus,'toolbonus_display':bonus,'options-flag':0,
                             'toolroll':self.simple_roll(display,'toolbonus')})
        self.spell_slots()

    def spell_slots(self):
        caster_level=0
        casters=[c for c in self.classes if get(c,'system.spellcasting.progression') not in (None,'','none','pact')]
        for c in casters:
            n=number(get(c,'system.levels'));p=get(c,'system.spellcasting.progression')
            if p=='full':caster_level+=n
            elif p in ('half','artificer'):
                up=p=='artificer' or get(c,'system.source.rules')=='2024' or len(casters)==1
                caster_level+=math.ceil(n/2) if up else math.floor(n/2)
            elif p=='third':caster_level+=math.ceil(n/3) if len(casters)==1 else math.floor(n/3)
            else:self.warn(f'{c["name"]}: unsupported spell-slot progression {p!r}.')
        slot_table=SLOTS[min(20,int(caster_level))]
        for lv in range(1,10):
            data=get(self.system,f'spells.spell{lv}',{})
            total=data.get('override',data.get('max'))
            if total is None:total=slot_table[lv-1] if len(slot_table)>=lv else 0
            current=data.get('value',total)
            self.add(f'lvl{lv}_slots_total',total)
            # Despite its historical name, this is the SLOTS REMAINING field.
            self.add(f'lvl{lv}_slots_expended',min(number(current),number(total)))
            self.add(f'lvl{lv}_slots_mod',number(total)-(slot_table[lv-1] if len(slot_table)>=lv else 0))
        pact=[c for c in self.classes if get(c,'system.spellcasting.progression')=='pact']
        if pact:
            n=sum(number(get(c,'system.levels')) for c in pact)
            total=get(self.system,'spells.pact.max',1 if n==1 else 2 if n<11 else 3 if n<17 else 4)
            level=get(self.system,'spells.pact.level',min(5,math.ceil(n/2)))
            self.resource(f'Pact slots (level {level})',get(self.system,'spells.pact.value',total),total,[{'period':'sr','type':'recoverAll'}])

    def resource(self,name,current,maximum,recovery,resource_id=None):
        self.pending_resources.append((resource_id or 'system:'+slug(name),name,current,maximum,recovery))

    def write_resource(self,name,current,maximum,recovery):
        index=self.counts['resources'];self.counts['resources']+=1
        periods={r.get('period') for r in recovery if r.get('type')=='recoverAll'}
        reset='short' if 'sr' in periods else 'long' if 'lr' in periods else ''
        if index==0:
            self.add('other_resource_name',name);self.add('other_resource',current,maximum);self.add('other_resource_reset',reset);self.add('options-flag-other-resource',0)
        else:
            pair=(index-1)//2
            rowid=self.rows['resource'][pair] if pair<len(self.rows['resource']) else uid()
            side='left' if (index-1)%2==0 else 'right'
            self.row('resource',{f'resource_{side}_name':name,f'resource_{side}_reset':reset,f'options-flag-{side}':0},rowid)
            self.add(f'repeating_resource_{rowid}_resource_{side}',current,maximum)
        if recovery:
            self.notes.append(f'{name} recovery: '+', '.join(f"{r.get('period','?')} {r.get('type','')} {r.get('formula','')}".strip() for r in recovery))
            if any(r.get('type')!='recoverAll' or r.get('period') not in ('sr','lr') for r in recovery):
                self.warn(f'{name}: partial/dawn/rolled recovery must be applied manually; recovery rule is recorded in the report.')

    def uses(self,uses,label,ability=None,resource_id=None,default_selected=True):
        if uses.get('max') in (None,'',0,'0'):return
        selected=self.review.get('resources')
        if selected is not None and resource_id not in selected:return
        if selected is None and not default_selected:return
        maximum=self.numeric(uses['max'],ability,label+' maximum uses',default=-1)
        if maximum<0:return
        current=uses.get('value',max(0,maximum-number(uses.get('spent'))))
        recovery=uses.get('recovery',[])
        if uses.get('per'):recovery=[{'period':uses['per'],'type':'recoverAll'}]
        self.resource(label,min(number(current),maximum),maximum,recovery,resource_id)

    def activities(self,item):
        s=item['system'];acts=values(s.get('activities'))
        if acts:return sorted(acts,key=lambda a:number(a.get('sort')))
        old=s.get('actionType','')
        kind='attack' if old in ('mwak','rwak','msak','rsak') else 'heal' if old=='heal' else 'save' if old=='save' else 'damage' if get(s,'damage.parts') else 'utility'
        a={'type':kind,'_legacy':True,'damage':s.get('damage',{}),'name':''}
        if kind=='attack':a['attack']={'ability':s.get('ability'),'bonus':s.get('attackBonus'),'type':{'value':'ranged' if old.startswith('r') else 'melee'}}
        if kind=='save':a['save']={'ability':[get(s,'save.ability','')],'dc':{'calculation':get(s,'save.scaling','spellcasting'),'formula':get(s,'save.dc')}}
        return [a]

    def ability_for(self,item,activity):
        s=item['system'];is_spell=item['type']=='spell'
        ab=get(activity,'attack.ability') or s.get('ability')
        if ab in ABILITIES:return ab
        if is_spell:return self.spell_ability
        if ab=='spellcasting':return self.spell_ability
        kind=get(s,'type.value','')
        if 'fin' in s.get('properties',[]) or 'finesse' in s.get('properties',[]):return max(('str','dex'),key=lambda k:self.mods[k])
        return 'dex' if kind.endswith('R') or get(activity,'attack.type.value')=='ranged' else 'str'

    def damage_part(self,part,item,activity,ability):
        if isinstance(part,list):
            return {'formula':self.formula(part[0],ability,item['name']), 'type':str(part[1] if len(part)>1 else '').title(),'scaling':{}}
        if not isinstance(part,dict):return None
        custom=part.get('custom',{})
        n=number(part.get('number'));die=number(part.get('denomination'))
        scaling=part.get('scaling',{})
        if custom.get('enabled'):
            expr=custom.get('formula','')
        else:
            if item['type']=='spell' and number(get(item,'system.level'))==0 and scaling.get('mode') in ('whole','half'):
                step=sum(self.level>=v for v in (5,11,17))
                n+=step*number(scaling.get('number'),n)
            expr=f'{n}d{die}' if n and die else ''
            if part.get('bonus'):expr+=((' + ' if expr else '')+str(part['bonus']))
        if not expr:return None
        dmg=self.formula(expr,ability,item['name']+' damage')
        types=part.get('types',[])
        if len(types)>1:self.warn(f'{item["name"]}: damage type is a choice ({", ".join(types)}); select it manually when rolling.')
        kind=' / '.join('Temporary HP' if t=='temphp' else t.title() for t in types)
        return {'formula':dmg,'type':kind,'scaling':scaling,'die':die,'original':expr}

    def roll_data(self,item,act):
        s=item['system'];ab=self.ability_for(item,act);kind=act.get('type');is_spell=item['type']=='spell'
        atk=act.get('attack',{})
        classification=get(atk,'type.classification','spell' if is_spell else 'weapon')
        category=('r' if get(atk,'type.value')=='ranged' else 'm')+('sak' if is_spell or classification=='spell' else 'wak')
        prof=s.get('proficient')
        if prof is None:
            weapon_kind=get(s,'type.value','')
            owned=get(self.system,'traits.weaponProf.value',[])
            prof=is_spell or classification=='unarmed' or ('sim' in owned and weapon_kind.startswith('simple')) or ('mar' in owned and weapon_kind.startswith('martial')) or get(s,'type.baseItem') in owned
        attack_bonus=self.numeric(atk.get('bonus',s.get('attackBonus')),ab,item['name']+' attack bonus')
        attack_bonus+=self.numeric(get(self.system,f'bonuses.{category}.attack'),ab,'Global attack bonus')
        magic=self.numeric(get(s,'magicalBonus',get(s,'magicBonus',0)),ab,item['name']+' magic bonus')
        attack_bonus+=magic
        flat=atk.get('flat',False)
        total=attack_bonus if flat else self.mods.get(ab,0)+attack_bonus+(self.pb if prof else 0)
        parts=[]
        if kind=='heal' and act.get('healing'):parts.append(act['healing'])
        else:
            if get(act,'damage.includeBase',False) and get(s,'damage.base'):
                parts.append(get(s,'damage.base'))
            parts.extend(get(act,'damage.parts',[]))
        parsed=[self.damage_part(p,item,act,ab) for p in parts]
        parsed=[p for p in parsed if p and p['formula']]
        activity_id=f"{item.get('_id','')}:{act.get('_id','')}"
        edit=self.review.get('attacks',{}).get(activity_id,{})
        if edit.get('formula','').strip():
            formula=self.formula(edit['formula'],ab,item['name']+' reviewed damage')
            if formula:
                replacement={'formula':formula,'type':edit.get('type','').strip() or (parsed[0]['type'] if parsed else ''),'scaling':{},'die':0,'original':formula}
                if parsed:parsed[0]=replacement
                else:parsed.append(replacement)
        if magic and parsed and not is_spell:
            parsed[0]['formula']+=f' + {magic}'
        global_damage=get(self.system,f'bonuses.{category}.damage','')
        if global_damage and parsed:
            bonus=self.formula(global_damage,ab,'Global damage bonus')
            if bonus:parsed[0]['formula']+=' + '+bonus
        if kind=='heal':
            for p in parsed:
                if not p['type']:p['type']='Healing'
        dc_conf=get(act,'save.dc',{})
        calculation=dc_conf.get('calculation','spellcasting')
        dc= self.numeric(dc_conf.get('formula'),ab,item['name']+' save DC') if calculation in ('','flat') else 8+self.pb+self.mods.get(calculation,self.spellmod)+self.spell_dc_bonus
        saves=get(act,'save.ability',[])
        if isinstance(saves,str):saves=[saves]
        save=' or '.join(ABILITIES.get(k,k).title() for k in saves if k)
        if len(saves)>1:self.warn(f'{item["name"]}: target chooses {save}; choice is printed in the attack card, but the Roll20 single-ability dropdown cannot represent both choices.')
        if kind=='save' and not save:self.warn(f'{item["name"]}: saving throw ability missing; review the action.')
        rng=act.get('range') if get(act,'range.override',False) else s.get('range',{})
        rng=rng or {}
        range_text=(rng.get('units','').title() if rng.get('units') in ('self','touch','any','spec') else f"{rng.get('value','')} {rng.get('units','')}".strip())
        return dict(ability=ab,attack=kind=='attack',total=total,bonus=attack_bonus,proficient=bool(prof) and not flat,flat=flat,
                    parts=parsed,save=save,dc=dc,dc_mode=calculation,range=range_text,kind=kind,
                    on_save={'half':'Half damage','none':'No damage','full':'Full damage'}.get(get(act,'damage.onSave'),'See description'),
                    crit=number(get(atk,'critical.threshold'),20))

    def spell_signature(self,item,act,data=None):
        data=data or self.roll_data(item,act)
        # Utility/transform activities without dice or a save are equivalent
        # spell-card modes. Damage/healing formulas and DCs remain distinct.
        mechanical_kind=data['kind'] if data['parts'] or data['save'] else 'card'
        return (item['name'].casefold(),int(number(get(item,'system.level'))),mechanical_kind,data['dc'] if data['save'] else None,
                tuple((re.sub(r'\s+','',p['formula']).casefold(),p['type'].casefold()) for p in data['parts']))

    def attack_row(self,item,act,label,data,spell_id=None,spell_level=None):
        ab=data['ability'];parts=data['parts'];attack=data['attack'];save=data['save']
        if len(parts)>2:
            self.warn(f'{label}: more than two damage parts; additional parts are included in the description as inline rolls. Review after editing the sheet.')
        desc=clean(get(item,'system.description.value',''))
        if len(parts)>2:desc+='\n\nAdditional damage: '+', '.join(f"[[{p['formula']}]] {p['type']}" for p in parts[2:])
        abref='spell' if item['type']=='spell' and ab==self.spell_ability else f'@{{{ABILITIES[ab]}_mod}}' if ab else '0'
        # Fixed custom formulas already include any requested ability bonus. Do not add it again.
        fields={'atkname':label,'atkflag':'{{attack=1}}' if attack else 0,'atkattr_base':'0' if data['flat'] else abref,
                'atkprofflag':'(@{pb})' if data['proficient'] else 0,'atkmod':data['bonus'],'atkmagic':0,
                'atkbonus':f'{data["total"]:+g}' if attack else f'DC{data["dc"]}' if save else '-',
                'atkcritrange':data['crit'],'atkrange':data['range'],'atk_desc':desc,'options-flag':0,
                'saveflag':'{{save=1}} {{saveattr=@{saveattr}}} {{savedesc=@{saveeffect}}} {{savedc=[[@{savedc}]]}}' if save else 0,
                'saveattr':save,'saveeffect':data['on_save'],'savedc':'(@{spell_save_dc})' if data['dc_mode']=='spellcasting' and data['dc']==self.dc else '(@{saveflat})',
                'saveflat':data['dc'],'spellid':spell_id or '', 'spelllevel':spell_level if spell_level is not None else '',
                'spell_innate':'','hldmg':'','ammo':''}
        for index in range(2):
            prefix='dmg' if index==0 else 'dmg2'
            p=parts[index] if len(parts)>index else {'formula':'0','type':''}
            fields.update({prefix+'base':p['formula'],prefix+'attr':'0',prefix+'mod':0,prefix+'type':p['type'],
                           prefix+'flag':f'{{{{damage=1}}}} {{{{dmg{index+1}flag=1}}}}' if len(parts)>index else 0,
                           prefix+'custcrit':self.critical_dice(p['formula'])})
        fields['atkdmgtype']=' + '.join(f"{p['formula']} {p['type']}" for p in parts)
        macro='@{wtype}&{template:atkdmg} {{rname=@{atkname}}} {{mod=@{atkbonus}}} {{normal=1}}'
        if attack:macro+=' {{attack=1}} {{r1=[[1d20cs>@{atkcritrange}'+f'+{data["total"]}'+']]}}'
        macro+=' {{range=@{atkrange}}}'
        for index,p in enumerate(parts[:2],1):
            macro+=' {{damage=1}} {{dmg'+str(index)+'flag=1}} {{dmg'+str(index)+'=[['+p['formula']+']]}} {{dmg'+str(index)+'type='+p['type']+'}}'
            if attack:macro+=f' {{{{crit{index}=[[{self.critical_dice(p["formula"])}]]}}}}'
        if save:macro+=' @{saveflag}'
        if spell_level and isinstance(spell_level,int) and spell_level>0 and parts:
            scale=parts[0].get('scaling',{});die=parts[0].get('die')
            if scale.get('mode')=='whole' and scale.get('number') and die:
                query='?{Cast at what level?'+''.join(f'|Level {n},{n-spell_level}' for n in range(spell_level,10))+'}'
                fields['hldmg']=f'{{{{hldmg=[[({scale["number"]}*{query})d{die}]]}}}}'
            elif scale.get('mode')=='whole' and scale.get('formula') and number(scale.get('formula'),None) is not None:
                query='?{Cast at what level?'+''.join(f'|Level {n},{n-spell_level}' for n in range(spell_level,10))+'}'
                fields['hldmg']='{{hldmg=[['+str(scale['formula'])+'*'+query+']]}}'
            elif scale.get('mode'):
                self.warn(f'{label}: nonstandard upcasting requires manual adjustment; see spell description.')
        macro+=' @{hldmg} {{desc=@{atk_desc}}} @{charname_output}'
        fields['rollbase']=macro
        fields['rollbase_dmg']=macro.replace('template:atkdmg','template:dmg')
        fields['rollbase_crit']=fields['rollbase_dmg']+' {{crit=1}}'
        rowid=self.row('attack',fields)
        self.attack_row_keys[rowid]=f"{item.get('_id','')}:{act.get('_id','')}"
        self.counts['attacks']+=1
        return rowid

    @staticmethod
    def critical_dice(formula):
        dice=re.findall(r'\b\d*d\d+(?:k[hl]\d+)?\b',str(formula),re.I)
        return ' + '.join(dice) or '0'

    def spell_row(self,item,act,label,data,rollable):
        s=item['system'];level=int(number(s.get('level')))
        if not 0<=level<=9:
            self.warn(f'{label}: invalid spell level {level}, retained under level 9.');level=9
        group='cantrip' if level==0 else level
        section=f'spell-{group}';rowid=uid();desc=clean(get(s,'description.value',''))
        props=s.get('properties',[])
        components=s.get('components',{})
        act_time=act.get('activation') if get(act,'activation.override') else s.get('activation',{})
        act_time=act_time or {}
        duration=act.get('duration') if get(act,'duration.override') else s.get('duration',{})
        duration=duration or {}
        target=act.get('target') if get(act,'target.override') else s.get('target',{})
        target=target or {}
        template=target.get('template',{})
        target_text=f"{template.get('size','')} {template.get('units','')} {template.get('type','')}".strip() if template.get('type') else f"{get(target,'affects.count','')} {get(target,'affects.type','')}".strip()
        ability=data['ability']
        spell_ability='spell' if ability==self.spell_ability else f'@{{{ABILITIES[ability]}_mod}}+' if ability else '0*'
        # One row per actionable mode; no loss of second/third activities.
        fields={'spellname':label,'spelllevel':group,'spellschool':SCHOOLS.get(s.get('school'),s.get('school','')),
                'spell_ability':spell_ability,'spelloutput':'ATTACK' if rollable else 'SPELLCARD',
                'spellattack':('Ranged' if get(act,'attack.type.value')=='ranged' else 'Melee') if data['attack'] else 'None',
                'spellsave':data['save'],'spellsavesuccess':data['on_save'] if data['save'] else '',
                'spellrange':data['range'],'spelltarget':target_text,
                'spellcastingtime':f"{act_time.get('value',1)} {act_time.get('type','action')}",
                'spellduration':'Instantaneous' if duration.get('units')=='inst' else f"{duration.get('value','')} {duration.get('units','')}",
                'spelldescription':desc,'spellathigherlevels':'','spellprepared':1 if level==0 or number(s.get('prepared'))>0 or get(s,'preparation.prepared') else 0,
                'spellcomp_materials':get(s,'materials.value',''),'spellconcentration':'{{concentration=1}}' if 'concentration' in props or components.get('concentration') else 0,
                'spellritual':'{{ritual=1}}' if 'ritual' in props or components.get('ritual') else 0,
                'spell_damage_progression':'','spelldmgmod':'','spellhealing':'','spellhldie':'','spellhldietype':'','spellhlbonus':'',
                'spellclass':s.get('sourceClass',s.get('sourceItem','')),'spellsource':get(s,'source.book',''),
                'includedesc':'on','innate':'','options-flag':0,'details-flag':0,'roll_output_dc':data['dc'] if data['save'] else 0}
        for ch,key in [('v','vocal'),('s','somatic'),('m','material')]:fields['spellcomp_'+ch]=f'{{{{{ch}=1}}}}' if key in props or components.get(key) else 0
        parts=data['parts']
        for n in range(2):
            suffix='' if n==0 else '2';p=parts[n] if len(parts)>n else {'formula':'','type':''}
            fields['spelldamage'+suffix]=p['formula'];fields['spelldamagetype'+suffix]=p['type']
        if parts and parts[0]['type']=='Healing':fields['spellhealing']=parts[0]['formula'];fields['spelldamage']='';fields['spelldamagetype']=''
        if level>0 and parts:
            scale=parts[0].get('scaling',{});die=parts[0].get('die')
            if scale.get('mode')=='whole' and scale.get('number') and die:
                fields['spellhldie']=scale['number'];fields['spellhldietype']='d'+str(die)
            elif scale.get('mode')=='whole' and scale.get('formula') and number(scale.get('formula'),None) is not None:
                # Worker requires a die term to build hldmg; 0d4 adds no dice.
                fields['spellhldie']='0';fields['spellhldietype']='d4';fields['spellhlbonus']=scale['formula']
        if rollable:
            attack_id=self.attack_row(item,act,label,data,rowid,group)
            fields['spellattackid']=attack_id
            # Name-based link survives VTTES regenerating the character's database ID.
            fields['rollcontent']=f'%{{{self.actor["name"]}|repeating_attack_{attack_id}_attack}}'
            if data['dc_mode'] in ('','flat'):
                self.warn(f'{label}: fixed save DC {data["dc"]} is preserved in its linked attack; editing the spell can reset it to the sheet DC.')
        else:
            fields['rollcontent']='@{wtype}&{template:spell} {{name=@{spellname}}} {{level=@{spellschool} @{spelllevel}}} {{castingtime=@{spellcastingtime}}} {{range=@{spellrange}}} {{target=@{spelltarget}}} @{spellcomp_v} @{spellcomp_s} @{spellcomp_m} {{material=@{spellcomp_materials}}} {{duration=@{spellduration}}} {{description=@{spelldescription}}} @{spellritual} @{spellconcentration} @{charname_output}'
        self.row(section,fields,rowid)
        self.counts['spell_rows']+=1

    def convert_items(self):
        lookup={i.get('_id'):i['name'] for i in self.items}
        duplicate_names=Counter(i['name'] for i in self.items if i['type']=='spell')
        occurrences=Counter()
        seen_spell_signatures=set()
        for item in self.items:
            s=item['system'];kind=item['type'];name=item['name'];desc=clean(get(s,'description.value',''))
            if kind not in PHYSICAL|{'feat','spell','race','class','subclass','background'}:
                self.warn(f'{name}: unsupported item type {kind}; retained as a trait.')
            if kind=='feat' or kind not in PHYSICAL|{'spell','race','class','subclass','background'}:
                self.row('traits',{'name':name,'description':desc,'source':'Feat','source_type':get(s,'source.book',''), 'options-flag':0,'display_flag':'on'})
                self.counts['features']+=1
            inv_id=None
            if kind in PHYSICAL:
                container=lookup.get(s.get('container'),'')
                if container:desc+='\n\nContainer: '+container
                self.row('inventory',{},inv_id:=uid())
                self.row('inventory',{'itemname':name,'itemcount':s.get('quantity',1),'itemweight':get(s,'weight.value',s.get('weight',0)),
                                     'itemcontent':desc,'itemproperties':', '.join(s.get('properties',[])),
                                     'equipped':1 if s.get('equipped') else 0,'itemmodifiers':'','hasattack':0,'options-flag':0},inv_id)
                self.counts['inventory']+=1
            item_id=item.get('_id','')
            self.uses(s.get('uses',{}),name,s.get('ability'),'item:'+item_id,kind not in {'consumable','loot','tool','container','backpack'})
            acts=self.activities(item)
            rollables=[a for a in acts if a.get('type') in ROLLABLE]
            for a in acts:
                aid=f"{item_id}:{a.get('_id','')}"
                self.uses(a.get('uses',{}),name+' — '+(a.get('name') or a.get('type','Activity')),s.get('ability'),'activity:'+aid,False)
            if kind=='spell':
                self.counts['spells']+=1;occurrences[name]+=1
                label=name
                cached=get(item,'flags.dnd5e.cachedFor','')
                if cached:
                    m=re.search(r'Item\.([^.]+)',cached)
                    source=lookup.get(m[1],'Magic item') if m else 'Magic item'
                    label+=f' [{source}]'
                elif duplicate_names[name]>1 and occurrences[name]>1:label+=f' [{occurrences[name]}]'
                candidates=rollables or acts[:1]
                explicitly=self.review.get('spell_activities',{})
                candidates=[a for a in candidates if explicitly.get(f"{item_id}:{a.get('_id','')}",True)]
                mode=self.review.get('spell_mode','unique')
                if mode=='first':candidates=candidates[:1]
                elif mode=='unique':
                    unique=[];seen=set()
                    for a in candidates:
                        d=self.roll_data(item,a)
                        signature=self.spell_signature(item,a,d)
                        if signature in seen or signature in seen_spell_signatures:continue
                        seen.add(signature);seen_spell_signatures.add(signature);unique.append(a)
                    candidates=unique
                for a in candidates:
                    rowlabel=label+(' — '+(a.get('name') or a['type'].title()) if len(rollables)>1 else '')
                    self.spell_row(item,a,rowlabel,self.roll_data(item,a),bool(rollables))
                unsupported={a['type'] for a in acts if a['type'] not in ROLLABLE|{'utility'}}
                if unsupported:self.warn(f'{label}: {", ".join(sorted(unsupported))} automation is not portable; full spell text retained.')
            elif kind in PHYSICAL|{'feat'}:
                attack_ids=[]
                for a in rollables:
                    aid=f"{item_id}:{a.get('_id','')}"
                    if not self.review.get('attack_include',{}).get(aid,True):continue
                    label=name+(' — '+(a.get('name') or a['type'].title()) if len(rollables)>1 else '')
                    attack_ids.append(self.attack_row(item,a,label,self.roll_data(item,a)))
                if inv_id and attack_ids:
                    self.row('inventory',{'hasattack':1,'itemattackid':','.join(attack_ids)},inv_id)
                    # Keep complex item mechanics from being regenerated by the simple inventory parser.
                    self.notes.append(f'{name}: attacks linked from inventory; changing item modifiers may require reviewing those attacks.')

    def run(self):
        self.core();self.convert_items()
        resource_order=self.review.get('resource_order',[])
        resource_rank={key:i for i,key in enumerate(resource_order)}
        self.pending_resources.sort(key=lambda r:(resource_rank.get(r[0],len(resource_rank)),r[1].casefold()))
        pending=self.pending_resources;self.pending_resources=[]
        for _,name,current,maximum,recovery in pending:self.write_resource(name,current,maximum,recovery)
        attack_order=self.review.get('attack_order',[]);attack_rank={key:i for i,key in enumerate(attack_order)}
        if attack_order:self.rows['attack'].sort(key=lambda row:attack_rank.get(self.attack_row_keys.get(row,''),len(attack_rank)))
        for section,ids in self.rows.items():
            if section.startswith('spell-'):
                ids.sort(key=lambda row:self.attrs.get(f'repeating_{section}_{row}_spellname',{}).get('current','').casefold())
        for section,ids in self.rows.items():self.add('_reporder_repeating_'+section,','.join(ids))
        image=self.actor.get('img','')
        if image and not image.startswith(('https://','http://')):
            self.warn('Portrait uses a Foundry-relative path. Upload the portrait separately in Roll20.');image=''
        elif image:self.notes.append('Portrait URL retained; availability in Roll20 depends on the image host.')
        self.warn('Foundry macros, combat automation, summons, transformations, and automatic resource consumption are not executed in Roll20. Descriptions and supported roll entries are retained.')
        self.notes.append('Active effect bonuses are a snapshot. Reconvert after changing Foundry effects, levels, or equipment.')
        report={'converter_version':VERSION,'character':self.actor['name'],'source_version':self.actor.get('_stats',{}),
                'counts':dict(self.counts),'summary':{'level':self.level,'abilities':self.scores,'proficiency':self.pb,
                'hp':self.attrs['hp']['current'],'hp_max':self.attrs['hp']['max'],'ac':self.attrs['ac']['current'],
                'initiative':self.attrs['initiative_bonus']['current'],'speed':self.attrs['speed']['current'],
                'spell_ability':self.spell_ability,'spell_dc':self.dc,
                'skills':{long:{'bonus':self.attrs[long+'_bonus']['current'],'proficient':self.attrs[long+'_prof']['current']!='0'} for long,_ in SKILLS.values()}},
                'warnings':self.warnings,'notes':self.notes}
        output={'schema_version':3,'type':'character','character':{'oldId':self.character_id,'name':self.actor['name'],
                'avatar':image,'bio':clean(get(self.system,'details.biography.value','')),'gmnotes':'Converted from Foundry with FoundryRoll20Converter '+VERSION+'. See conversion report.',
                'defaulttoken':'','tags':'[]','controlledby':'','inplayerjournals':'','attribs':list(self.attrs.values()),'abilities':[]}}
        return output,report


def convert(actor,spell_ability=None,overrides=None,review=None):
    return Converter(actor,spell_ability,overrides,review).run()


def review_manifest(actor,spell_ability=None):
    """Return the serializable review data used by browser front ends."""
    probe=Converter(actor,spell_ability)
    resources=[]
    attacks=[]
    groups={}
    for item in actor['items']:
        uses=get(item,'system.uses',{});maximum=uses.get('max')
        if maximum not in (None,'',0,'0'):
            resources.append({'id':'item:'+item.get('_id',''),'name':item['name'],'type':item['type'],'maximum':str(maximum),
                              'selected':item['type'] not in {'consumable','loot','tool','container','backpack'}})
        acts=probe.activities(item)
        candidates=[a for a in acts if a.get('type') in ROLLABLE]
        if item['type']=='spell':candidates=candidates or acts[:1]
        for act in candidates:
            aid=f"{item.get('_id','')}:{act.get('_id','')}";data=probe.roll_data(item,act)
            label=item['name']+(' — '+(act.get('name') or act.get('type','').title()) if len(acts)>1 else '')
            if item['type']=='spell':
                key=probe.spell_signature(item,act,data)
                groups.setdefault(key,[]).append({'id':aid,'label':label,'level':int(number(get(item,'system.level'))),'data':data})
            else:
                attacks.append({'id':aid,'label':label,'kind':act.get('type',''),'formula':data['parts'][0]['formula'] if data['parts'] else '',
                                'damage_type':data['parts'][0]['type'] if data['parts'] else ''})
    spells=[]
    for key,group in sorted(groups.items(),key=lambda pair:(pair[0][1],pair[0][0],pair[0][2],str(pair[0][4]))):
        first=group[0];data=first['data']
        summary=', '.join(p['formula']+' '+p['type'] for p in data['parts']) or (f"DC {data['dc']} {data['save']}" if data['save'] else data['kind'])
        spells.append({'ids':[x['id'] for x in group],'label':first['label'],'level':first['level'],'summary':summary,'copies':len(group)})
    s=get(actor,'system',{});senses=get(s,'attributes.senses',{});ranges=dict(senses.get('ranges',{}));ranges.update({k:v for k,v in senses.items() if k in ('darkvision','blindsight','tremorsense','truesight')})
    defenses={'senses':', '.join(f'{k.title()} {v} {senses.get("units") or "ft"}' for k,v in ranges.items() if v)}
    if senses.get('special'):defenses['senses']+=('; ' if defenses['senses'] else '')+senses['special']
    for key in ('di','dr','ci','dv'):
        trait=get(s,'traits.'+key,{});text=', '.join(str(v).replace('_',' ').title() for v in trait.get('value',[]))
        if trait.get('custom'):text+=('; ' if text else '')+trait['custom']
        defenses[key]=text
    return {'character':actor['name'],'spell_ability':probe.spell_ability,'resources':resources,'attacks':attacks,'spells':spells,'defenses':defenses}


def report_text(report):
    lines=[f"Foundry → Roll20 conversion report | v{VERSION}",report['character'],'',json.dumps(report['summary'],ensure_ascii=False,indent=2),
           '\nCOUNTS',json.dumps(report['counts'],indent=2),'\nREVIEW ITEMS']
    lines.extend('- '+w for w in report['warnings'])
    lines.append('\nCONVERSION NOTES');lines.extend('- '+n for n in report['notes'])
    lines.append('\nValidation: file structure and local calculations only. A live import and roll in your Roll20 game is still required.')
    return '\n'.join(lines)+'\n'


def save_conversion(input_path,output_path=None,spell_ability=None,overrides=None,overwrite=False,review=None):
    source=Path(input_path).expanduser().resolve()
    target=Path(output_path).expanduser().resolve() if output_path else source.with_name(source.stem+'-roll20.json')
    if target.suffix.lower()!='.json':target=target.with_suffix('.json')
    if target==source:raise ValueError('Output must be a different file from the Foundry input.')
    report_path=target.with_name(target.stem+'-report.txt')
    if not overwrite and (target.exists() or report_path.exists()):raise ValueError('Output or report already exists. Choose another name, or use --overwrite.')
    with source.open(encoding='utf-8-sig') as f:actor=json.load(f)
    output,report=convert(actor,spell_ability,overrides,review)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    report_path.write_text(report_text(report),encoding='utf-8')
    return target,report_path,report


def gui():
    try:
        import tkinter as tk
        from tkinter import ttk,filedialog,messagebox
    except ImportError:
        raise SystemExit('Tkinter is missing. Use Python from python.org (includes Tk), or run the command-line converter with --help.')
    root=tk.Tk();root.title('Foundry → Roll20 | D&D 2014');root.geometry('850x710');root.minsize(680,580)
    frame=ttk.Frame(root,padding=22);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Foundry → Roll20',font=('Helvetica',24,'bold')).pack(anchor='w')
    ttk.Label(frame,text='D&D 2014 sheets · VTTES / BetterR20 import · Files stay on your computer').pack(anchor='w',pady=(4,18))
    path=tk.StringVar();out=tk.StringVar();ability=tk.StringVar(value='Auto-detect');review={}
    for label,var,is_input in [('Foundry Actor JSON',path,True),('Save Roll20 JSON as',out,False)]:
        ttk.Label(frame,text=label).pack(anchor='w')
        line=ttk.Frame(frame);line.pack(fill='x',pady=(3,12))
        ttk.Entry(line,textvariable=var).pack(side='left',fill='x',expand=True)
        def browse(v=var,inp=is_input):
            p=filedialog.askopenfilename(filetypes=[('JSON files','*.json'),('All files','*')]) if inp else filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('JSON files','*.json')])
            if p:
                v.set(p)
                if inp:
                    out.set(str(Path(p).with_name(Path(p).stem+'-roll20.json')))
                    review.clear()
        ttk.Button(line,text='Browse…',command=browse).pack(side='left',padx=(8,0))
    ttk.Label(frame,text='Spellcasting ability').pack(anchor='w')
    ttk.Combobox(frame,textvariable=ability,values=['Auto-detect']+[f'{k.upper()} — {v.title()}' for k,v in ABILITIES.items()],state='readonly',width=30).pack(anchor='w',pady=(3,12))
    def review_conversion():
        if not path.get():messagebox.showerror('Choose a file','Choose a Foundry Actor JSON first.');return
        try:
            actor=json.loads(Path(path.get()).read_text(encoding='utf-8-sig'))
            ab=None if ability.get()=='Auto-detect' else ability.get()[:3].lower()
            probe=Converter(actor,ab)
        except Exception as e:messagebox.showerror('Could not review',str(e));return
        win=tk.Toplevel(root);win.title('Review conversion');win.geometry('980x720');win.transient(root);win.grab_set()
        book=ttk.Notebook(win);book.pack(fill='both',expand=True,padx=12,pady=12)
        general=ttk.Frame(book,padding=12);book.add(general,text='Spells and traits')
        ttk.Label(general,text='Repeated spell effects').grid(row=0,column=0,sticky='w',pady=5)
        mode=tk.StringVar(value=review.get('spell_mode','unique'))
        ttk.Combobox(general,textvariable=mode,state='readonly',values=['unique','all','first'],width=20).grid(row=0,column=1,sticky='w')
        ttk.Label(general,text='unique keeps one entry when damage dice and DC match; all keeps every activity.').grid(row=1,column=0,columnspan=2,sticky='w')
        defense_vars={}; designated=review.get('defenses',{})
        labels=[('di','Damage Immunities'),('dr','Damage Resistances'),('ci','Condition Immunities'),('dv','Damage Vulnerabilities'),('senses','Special Senses')]
        senses=get(actor,'system.attributes.senses',{});ranges=dict(senses.get('ranges',{}));ranges.update({k:v for k,v in senses.items() if k in ('darkvision','blindsight','tremorsense','truesight')})
        defaults={'senses':', '.join(f'{k.title()} {v} {senses.get("units") or "ft"}' for k,v in ranges.items() if v)}
        for key,_ in labels[:-1]:
            tr=get(actor,'system.traits.'+key,{});defaults[key]=', '.join(str(v).replace('_',' ').title() for v in tr.get('value',[]))
            if tr.get('custom'):defaults[key]+=('; ' if defaults[key] else '')+tr['custom']
        if senses.get('special'):defaults['senses']+=('; ' if defaults['senses'] else '')+senses['special']
        for row,(key,label) in enumerate(labels,2):
            ttk.Label(general,text=label).grid(row=row,column=0,sticky='w',padx=(0,10),pady=5)
            defense_vars[key]=tk.StringVar(value=designated.get(key,defaults.get(key,'')))
            ttk.Entry(general,textvariable=defense_vars[key],width=90).grid(row=row,column=1,sticky='ew',pady=5)
        general.columnconfigure(1,weight=1)

        def scrolling_tab(title):
            holder=ttk.Frame(book);book.add(holder,text=title);canvas=tk.Canvas(holder,highlightthickness=0);bar=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview)
            body=ttk.Frame(canvas,padding=8);window=canvas.create_window((0,0),window=body,anchor='nw');canvas.configure(yscrollcommand=bar.set)
            body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')));canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
            canvas.pack(side='left',fill='both',expand=True);bar.pack(side='right',fill='y');return body
        resources=scrolling_tab('Resources');resource_vars={};resource_order_vars={}
        ttk.Label(resources,text='Check the resources to export. Set Order to control their position (1 appears first).').grid(row=0,column=0,columnspan=4,sticky='w',pady=(0,8))
        ttk.Label(resources,text='Order',font=('Helvetica',10,'bold')).grid(row=1,column=0,sticky='w');ttk.Label(resources,text='Include / resource',font=('Helvetica',10,'bold')).grid(row=1,column=1,sticky='w')
        rr=2;resource_position={key:i+1 for i,key in enumerate(review.get('resource_order',[]))}
        for item in actor['items']:
            uses=get(item,'system.uses',{});mx=uses.get('max');iid='item:'+item.get('_id','')
            if mx not in (None,'',0,'0'):
                default=item['type'] not in {'consumable','loot','tool','container','backpack'}
                var=tk.BooleanVar(value=iid in review.get('resources',[]) if 'resources' in review else default);resource_vars[iid]=var
                order=tk.IntVar(value=resource_position.get(iid,rr-1));resource_order_vars[iid]=order;ttk.Spinbox(resources,from_=1,to=999,textvariable=order,width=6).grid(row=rr,column=0,sticky='w',padx=4)
                ttk.Checkbutton(resources,text=item['name'],variable=var).grid(row=rr,column=1,sticky='w');ttk.Label(resources,text=f"{item['type']} · max {mx}").grid(row=rr,column=2,sticky='w',padx=10);rr+=1
        attacks=scrolling_tab('Core attacks');attack_vars={};attack_order_vars={};spell_vars={}
        ttk.Label(attacks,text='Edit damage, clear Include, or set Order to rearrange Core-tab entries (1 appears first).').grid(row=0,column=0,columnspan=6,sticky='w',pady=(0,8))
        for col,label in enumerate(('Order','Include','Entry','Type','Primary damage','Damage type')):ttk.Label(attacks,text=label,font=('Helvetica',10,'bold')).grid(row=1,column=col,sticky='w',padx=4)
        ar=2;attack_position={key:i+1 for i,key in enumerate(review.get('attack_order',[]))}
        for item in actor['items']:
            acts=probe.activities(item)
            for act in acts:
                if act.get('type') not in ROLLABLE:continue
                aid=f"{item.get('_id','')}:{act.get('_id','')}";data=probe.roll_data(item,act)
                if item['type']=='spell':
                    var=tk.BooleanVar(value=review.get('spell_activities',{}).get(aid,True));spell_vars[aid]=var;continue
                inc=tk.BooleanVar(value=review.get('attack_include',{}).get(aid,True));formula=tk.StringVar(value=review.get('attacks',{}).get(aid,{}).get('formula',data['parts'][0]['formula'] if data['parts'] else ''));dtype=tk.StringVar(value=review.get('attacks',{}).get(aid,{}).get('type',data['parts'][0]['type'] if data['parts'] else ''))
                attack_vars[aid]=(inc,formula,dtype);order=tk.IntVar(value=attack_position.get(aid,ar-1));attack_order_vars[aid]=order;ttk.Spinbox(attacks,from_=1,to=999,textvariable=order,width=6).grid(row=ar,column=0,sticky='w',padx=4);ttk.Checkbutton(attacks,variable=inc).grid(row=ar,column=1)
                ttk.Label(attacks,text=item['name']+(' — '+(act.get('name') or act['type'].title()) if len(acts)>1 else '')).grid(row=ar,column=2,sticky='w',padx=4)
                ttk.Label(attacks,text=act['type']).grid(row=ar,column=3,sticky='w',padx=4);ttk.Entry(attacks,textvariable=formula,width=28).grid(row=ar,column=4,sticky='ew',padx=4);ttk.Entry(attacks,textvariable=dtype,width=18).grid(row=ar,column=5,sticky='ew',padx=4);ar+=1
        spells=scrolling_tab('Spell effects');sr=0
        ttk.Label(spells,text='These are the individual spell activities. Clear any effect you never want exported. The repeated-effect rule is applied afterward.').grid(row=sr,column=0,columnspan=2,sticky='w',pady=(0,8));sr+=1
        spell_groups={}
        for item in actor['items']:
            if item['type']!='spell':continue
            acts=[a for a in probe.activities(item) if a.get('type') in ROLLABLE] or probe.activities(item)[:1]
            for act in acts:
                data=probe.roll_data(item,act);key=probe.spell_signature(item,act,data)
                spell_groups.setdefault(key,[]).append((item,act,data,len(acts)))
        for _,group in sorted(spell_groups.items(),key=lambda pair:(pair[0][1],pair[0][0],pair[0][2],str(pair[0][4]))):
            item,act,data,act_count=group[0];ids=[f"{x[0].get('_id','')}:{x[1].get('_id','')}" for x in group]
            prior=review.get('spell_activities',{});var=tk.BooleanVar(value=any(prior.get(aid,True) for aid in ids))
            for aid in ids:spell_vars[aid]=var
            summary=', '.join(p['formula']+' '+p['type'] for p in data['parts']) or (f"DC {data['dc']} {data['save']}" if data['save'] else act.get('type',''))
            label=item['name']+(' — '+(act.get('name') or act.get('type','').title()) if act_count>1 else '')
            if len(group)>1:label+=f' ({len(group)} identical sources)'
            ttk.Checkbutton(spells,text=label,variable=var).grid(row=sr,column=0,sticky='w');ttk.Label(spells,text=summary).grid(row=sr,column=1,sticky='w',padx=10);sr+=1
        def accept():
            resource_order=sorted(resource_order_vars,key=lambda k:(resource_order_vars[k].get(),k));attack_order=sorted(attack_order_vars,key=lambda k:(attack_order_vars[k].get(),k))
            review.clear();review.update({'spell_mode':mode.get(),'defenses':{k:v.get().strip() for k,v in defense_vars.items()},'resources':[k for k,v in resource_vars.items() if v.get()],
                'resource_order':resource_order,'attack_order':attack_order,'attack_include':{k:v[0].get() for k,v in attack_vars.items()},'attacks':{k:{'formula':v[1].get().strip(),'type':v[2].get().strip()} for k,v in attack_vars.items()},'spell_activities':{k:v.get() for k,v in spell_vars.items()}});win.destroy()
        ttk.Button(win,text='Use these choices',command=accept).pack(anchor='e',padx=12,pady=(0,12))
    ttk.Button(frame,text='Review spells, attacks, resources, and traits…',command=review_conversion).pack(anchor='w',pady=(0,12))
    box=ttk.LabelFrame(frame,text='Optional corrections — leave blank to calculate from Foundry',padding=10);box.pack(fill='x')
    vars={}
    fields=[('ac','AC'),('hp_max','Max HP'),('hp_current','Current HP'),('speed','Walk speed'),('initiative','Initiative bonus')]
    for col,(key,label) in enumerate(fields):
        ttk.Label(box,text=label).grid(row=0,column=col,padx=4,sticky='w')
        var=tk.StringVar();vars[key]=var;ttk.Entry(box,textvariable=var,width=12).grid(row=1,column=col,padx=4,sticky='ew');box.columnconfigure(col,weight=1)
    ttk.Label(frame,text='Conversion report',font=('Helvetica',12,'bold')).pack(anchor='w',pady=(15,5))
    logframe=ttk.Frame(frame);logframe.pack(fill='both',expand=True)
    log=tk.Text(logframe,wrap='word',height=12,padx=10,pady=10);log.pack(side='left',fill='both',expand=True)
    scroll=ttk.Scrollbar(logframe,command=log.yview);scroll.pack(side='right',fill='y');log.configure(yscrollcommand=scroll.set)
    log.insert('end','Choose a Foundry player-character export.\n\nThe converter imports skills, saves, inventory, traits, spells, attacks and resources. It reports unsupported mechanics for review.\n\nAfter conversion, import the JSON into your Roll20 game using VTTES / BetterR20, then check the sheet and roll a skill, weapon and spell.');log.configure(state='disabled')
    def do_convert():
        try:
            if not path.get() or not out.get():raise ValueError('Choose an input file and an output location.')
            overrides={k:float(v.get()) for k,v in vars.items() if v.get().strip()}
            for k,v in overrides.items():
                if not math.isfinite(v) or (k!='initiative' and v<0):raise ValueError('Corrections must be finite numbers; only initiative can be negative.')
            dest=Path(out.get()).with_suffix('.json')
            if dest.exists() or dest.with_name(dest.stem+'-report.txt').exists():
                if not messagebox.askyesno('Replace existing output?','The output or report already exists. Replace these files?'):return
            ab=None if ability.get()=='Auto-detect' else ability.get()[:3].lower()
            target,rep,report=save_conversion(path.get(),out.get(),ab,overrides,overwrite=True,review=review)
            log.configure(state='normal');log.delete('1.0','end');log.insert('end',f'Saved: {target}\nReport: {rep}\n\n'+report_text(report));log.configure(state='disabled')
        except Exception as e:messagebox.showerror('Conversion could not finish',str(e))
    ttk.Button(frame,text='Convert and save JSON',command=do_convert).pack(anchor='e',pady=(14,0))
    root.mainloop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',nargs='?',help='Foundry Actor JSON. Omit to open the app.')
    parser.add_argument('-o','--output',help='Output JSON path (default: input name + -roll20.json).')
    parser.add_argument('--spell-ability',choices=list(ABILITIES),help='Override primary spellcasting ability.')
    parser.add_argument('--overrides',help='Optional JSON file: ac, hp_max, hp_current, speed, initiative, or str/dex/con/int/wis/cha.')
    parser.add_argument('--review',help='Optional review-choice JSON created manually for scripted conversions.')
    parser.add_argument('--overwrite',action='store_true',help='Replace existing output and report (never the source).')
    parser.add_argument('--version',action='version',version=VERSION)
    args=parser.parse_args()
    if not args.input:gui();return
    try:
        overrides=json.loads(Path(args.overrides).read_text(encoding='utf-8-sig')) if args.overrides else None
        review=json.loads(Path(args.review).read_text(encoding='utf-8-sig')) if args.review else None
        target,report_path,report=save_conversion(args.input,args.output,args.spell_ability,overrides,args.overwrite,review)
        print(f'Saved {target}\nReport: {report_path}\n{report["counts"]}\nReview items: {len(report["warnings"])}')
    except (OSError,ValueError,TypeError) as e:
        parser.exit(1,f'Error: {e}\n')


if __name__=='__main__':main()
