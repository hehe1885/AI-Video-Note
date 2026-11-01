const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
    constructor(tag='div') {this.tag=tag;this.children=[];this.listeners={};this.attrs={};this.style={};this.value='';this.offsetWidth=288;this.offsetHeight=320;this.classes=new Set();this.classList={toggle:(name,on)=>on?this.classes.add(name):this.classes.delete(name)};}
    append(...items) {this.children.push(...items);}
    replaceChildren() {this.children=[];}
    setAttribute(name,value) {this.attrs[name]=value;}
    addEventListener(name,cb) {(this.listeners[name] ||= []).push(cb);}
    dispatchEvent(event) {(this.listeners[event.type]||[]).forEach(fn=>fn(event));}
    emit(type,extra={}) {const event={type,target:this,preventDefault(){this.prevented=true;},...extra};this.dispatchEvent(event);return event;}
    focus() {document.activeElement=this;}
    closest() {return {querySelector:()=>({textContent:'开始日期'})};}
    contains(target) {return this===target || this.children.some(child=>child.contains(target));}
    getBoundingClientRect() {return {left:100,top:400,bottom:430};}
}
const start=new Element('input'),end=new Element('input');
const document=new Element();
document.readyState='complete';document.body=new Element('body');document.documentElement={lang:'zh-CN'};
document.getElementById=id=>({historyStartDate:start,historyEndDate:end})[id];
document.createElement=tag=>new Element(tag);
const window=new Element();window.innerWidth=800;window.innerHeight=800;
const context={document,window,Intl,Date,Event:class {constructor(type,options){this.type=type;Object.assign(this,options);}}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../frontend/date-picker.js'),'utf8'),context);
const popup=document.body.children[0];
const grid=()=>popup.children[1].children.filter(node=>node.tag==='button');
const key=(node,key)=>node.emit('keydown',{key});
let changes=0;start.addEventListener('change',()=>changes++);
start.value='2024-02-29';
assert.equal(start.emit('click').prevented,true,'Suppress browser popup');
assert.equal(popup.style.top,"436px");assert.equal(popup.hidden,false);assert.equal(start.attrs['aria-expanded'],'true');
assert.match(popup.children[0].children[0].textContent,/2024.*2/);
assert.equal(document.activeElement.attrs['aria-pressed'],'true');
key(document.activeElement,'ArrowRight');
assert.match(popup.children[0].children[0].textContent,/2024.*3/);
document.activeElement.emit('click');
assert.equal(start.value,'2024-03-01');assert.equal(changes,1);assert.equal(popup.hidden,true);
assert.equal(document.activeElement,start);
start.value='2024-01-31';start.emit('click');key(document.activeElement,'PageDown');document.activeElement.emit('click');
assert.equal(start.value,'2024-02-29','Month navigation clamps to leap day');
start.emit('click');popup.children[2].children[0].emit('click');
assert.equal(start.value,'');assert.equal(changes,3);
start.emit('click');popup.children[2].children[1].emit('click');
const today=new Date();assert.equal(start.value,`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`);
start.emit('click');key(document,'Escape');assert.equal(popup.hidden,true);assert.equal(changes,4);
end.emit('click');document.emit('pointerdown',{target:new Element()});assert.equal(popup.hidden,true);
end.emit('click');document.emit('focusin',{target:start});assert.equal(popup.hidden,true);
end.emit('click');window.emit('resize');assert.equal(popup.hidden,true);
document.documentElement.lang='en';window.AIVideoNoteI18n={translate:text=>({'清除':'Clear','今天':'Today'})[text]||text};
end.value='2026-10-19';end.emit('click');
assert.equal(popup.children[0].children[0].textContent,'October 2026');
assert.equal(popup.children[2].children[0].textContent,'Clear');
assert.equal(grid().length,42);assert.ok(popup.style.left);
window.innerHeight=500;window.emit('ui-language-changed');assert.equal(popup.style.top,'436px','Always opens below the input');assert.equal(popup.style.maxHeight,'56px','Scroll instead of flipping upward');
console.log('Date picker selection, leap-day navigation, clear/today, dismissal and English labels passed');
