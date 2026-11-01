const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../frontend/motion.js'), 'utf8');
let reduce = false;
const timers = [];
const window = {
    matchMedia: () => ({ matches: reduce }),
    setTimeout: (callback, delay) => timers.push({ callback, delay }),
};
const motionDocument = {};
vm.runInNewContext(source, { window, document: motionDocument });
const motion = window.AIVideoNoteMotion;
function element() {
    return {
        classes: new Set(), properties: {}, removed: false,
        classList: { add(name) { this.owner.classes.add(name); } },
        style: { setProperty(name, value) { this.owner.properties[name] = value; } },
        remove() { this.removed = true; },
    };
}
function node() {
    const result = element();
    result.classList.owner = result;
    result.style.owner = result;
    return result;
}
function render(task) {
    const row = node(), badge = node();
    motion.prepareTasks([task]);
    motion.decorateTask(row, badge, task, 20);
    return { row, badge };
}
let result = render({ task_id: 'one', status: 'running' });
assert(result.row.classes.has('motion-new-task'));
assert.equal(result.row.properties['--motion-order'], '4');
result = render({ task_id: 'one', status: 'running' });
assert.equal(result.row.classes.size, 0, 'polling must not replay entry');
assert.equal(result.badge.classes.size, 0);
result = render({ task_id: 'one', status: 'completed' });
assert(result.badge.classes.has('motion-state-change'));
motion.prepareTasks([]);
assert(render({ task_id: 'one', status: 'completed' }).row.classes.has('motion-new-task'));
let toast = node();
motion.dismissToast(toast);
assert(toast.classes.has('motion-toast-out'));
assert.equal(toast.removed, false);
assert.equal(timers[0].delay, 180);
timers[0].callback();
assert(toast.removed);
assert.equal(motion.scrollBehavior(), 'smooth');
reduce = true;
result = render({ task_id: 'two', status: 'running' });
assert.equal(result.row.classes.size, 0);
assert.equal(result.badge.classes.size, 0);
result = render({ task_id: 'two', status: 'completed' });
assert.equal(result.badge.classes.size, 0);
toast = node();
motion.dismissToast(toast);
assert(toast.removed);
assert.equal(timers.length, 1);
assert.equal(motion.scrollBehavior(), 'auto');
delete window.matchMedia;
assert.equal(motion.scrollBehavior(), 'smooth', 'missing preference API keeps working');
async function checkPanels() {
    let applied = 'expanded', clock = 0, nextFrame = 0;
    const frames = new Map(), ghosts = [];
    window.requestAnimationFrame = callback => {frames.set(++nextFrame,callback); return nextFrame;};
    window.cancelAnimationFrame = id => frames.delete(id);
    function step() {clock += 16; const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(clock);}
    function flush() {for (let i=0; i<100 && frames.size; i++) step(); assert.equal(frames.size,0);}
    function fakeNode() {
        return {style: {setProperty(name,value) {this[name]=value;}},attrs:{id:'videoUrl',name:'test'},classList:{add(){}},
            hasAttribute(name){return Object.hasOwn(this.attrs,name);},matches(){return false;},
            removeAttribute(name){delete this.attrs[name];},setAttribute(name,value){this.attrs[name]=value;},
            remove(){this.removed=true;}};
    }
    const child = fakeNode();
    const naturalHeight = () => applied === 'collapsed' ? 180 : 320;
    const inner = {...fakeNode(),querySelector:()=>null,querySelectorAll:()=>[child],
        getBoundingClientRect:()=>({height:naturalHeight()}),
        cloneNode(){const ghost=fakeNode();ghost.child=fakeNode();ghost.querySelectorAll=()=>[ghost.child];ghosts.push(ghost);return ghost;}};
    const panel = {inert:false,style:{height:'',overflow:''},
        querySelector:selector=>selector==='.source-mode-inner'?inner:null,
        getBoundingClientRect:()=>({height:parseFloat(panel.style.height)||naturalHeight(),top:0}),
        appendChild(ghost){this.ghost=ghost;}};
    window.getComputedStyle=()=>({getPropertyValue:()=>''});
    motion.switchPanel(panel,()=>{applied='collapsed';});
    assert.equal(panel.inert,true);
    assert.equal(panel.style.height,'320px');
    step();
    const first=parseFloat(panel.style.height);
    assert(first<320 && first>180);
    let last=first;
    while(frames.size){step();const height=parseFloat(panel.style.height)||naturalHeight();assert(height<=last+.001 && height>=180-.001,'spring from rest must settle monotonically');last=height;}
    assert.equal(panel.inert,false);
    assert.equal(panel.style.height,'');
    assert.equal(panel.style.overflow,'');
    assert.equal(inner.style.transform,undefined);
    assert.equal(ghosts[0].removed,true);
    assert.equal(ghosts[0].attrs.id,undefined);
    assert.equal(ghosts[0].child.attrs.name,undefined);
    assert.equal(ghosts[0].inert,true);
    applied='expanded';
    motion.switchPanel(panel,()=>{applied='collapsed';});
    step();step();
    const reversingHeight=parseFloat(panel.style.height);
    motion.switchPanel(panel,()=>{applied='expanded';});
    assert.equal(parseFloat(panel.style.height),reversingHeight,'retargeting preserves current position');
    step();
    assert(parseFloat(panel.style.height)<reversingHeight,'retargeting preserves closing velocity before braking');
    assert.equal(ghosts[1].removed,true);
    flush();
    assert.equal(panel.inert,false);
    assert.equal(naturalHeight(),320);
    motion.switchPanel(panel,()=>{applied='collapsed';});
    motion.cancelPanel(panel);
    assert.equal(frames.size,0);
    assert.equal(ghosts[3].removed,true);
    assert.equal(panel.inert,false);
    window.matchMedia=()=>({matches:true});
    motion.switchPanel(panel,()=>{applied='expanded';});
    assert.equal(frames.size,0);
    assert.equal(applied,'expanded');
    window.matchMedia=()=>({matches:false});
    motion.switchPanel(panel,()=>{applied='collapsed';},{anchor:'top'});
    step();
    assert.equal(inner.style.transform,'translateY(0px)','sidebar content stays anchored at the top');
    flush();
    assert.equal(panel.inert,false);
    assert.equal(panel.style.height,'');
    motion.switchPanel({},()=>{applied='fallback';});
    assert.equal(applied,'fallback');
    delete window.getComputedStyle;
    delete window.requestAnimationFrame;
    delete window.cancelAnimationFrame;
}
function checkThemes() {
    const settings = fs.readFileSync(path.join(__dirname, '../frontend/settings.js'), 'utf8');
    const themeSource = settings.slice(settings.indexOf('let themeTransition ='), settings.indexOf('let noteDirectoryPickerBusy'));
    const classes = new Set();
    const root = { dataset: {theme: 'system'}, offsetHeight: 100,
        classList: {add: c => classes.add(c), remove: c => classes.delete(c)} };
    const doc = {documentElement: root, querySelectorAll: () => []};
    const themeWindow = {matchMedia: () => ({matches:false})};
    const context = vm.createContext({document:doc, window:themeWindow});
    vm.runInContext(themeSource, context);
    vm.runInContext("applyTheme('light')", context);
    assert.equal(root.dataset.theme, 'light');
    assert.equal(classes.size, 0);
    const callbacks = [];
    let skipped = 0;
    doc.startViewTransition = update => {
        callbacks.push(update);
        return {skipTransition: () => skipped++, ready: Promise.resolve(), finished: Promise.resolve()};
    };
    vm.runInContext("applyTheme('dark'); applyTheme('system'); applyTheme('dark')", context);
    callbacks[0](); callbacks[1]();
    assert.equal(root.dataset.theme, 'light', 'stale callbacks cannot overwrite latest theme');
    callbacks[2]();
    assert.equal(root.dataset.theme, 'dark');
    assert.equal(skipped, 2);
    assert.equal(classes.size, 0);
    themeWindow.matchMedia = () => ({matches:true});
    vm.runInContext("applyTheme('light')", context);
    assert.equal(root.dataset.theme, 'light', 'reduced motion switches immediately');
    assert.equal(callbacks.length, 3);
}
function checkScrollbarExit() {
    let inspect, observations=0;
    const added=[];
    const sidebar={dataset:{},children:[{},{}],clientHeight:600,scrollHeight:900,
        clientWidth:291,offsetWidth:300,clientTop:0,scrollTop:150,
        getBoundingClientRect:()=>({top:0,right:300}),addEventListener(){}};
    motionDocument.querySelector=()=>sidebar;
    motionDocument.createElement=()=>({style:{},attrs:{},listeners:{},
        setAttribute(name,value){this.attrs[name]=value;},
        addEventListener(name,callback){this.listeners[name]=callback;},remove(){this.removed=true;}});
    motionDocument.body={appendChild:node=>added.push(node)};
    window.ResizeObserver=class {constructor(callback){inspect=callback;}observe(){observations++;}};
    window.addEventListener=()=>{};
    window.getComputedStyle=()=>({borderRightWidth:'1px',borderLeftWidth:'0px'});
    window.matchMedia=()=>({matches:false});
    motion.initSidebarScrollbarExit();
    assert.equal(observations,3);
    assert.equal(added.length,0,'normal native scrolling must not create a visual copy');
    sidebar.scrollHeight=600;inspect();
    assert.equal(added.length,1);
    assert.equal(added[0].style.height,'400px');
    assert.equal(added[0].attrs['aria-hidden'],'true');
    inspect();assert.equal(added.length,1,'only one fade when overflow ends');
    sidebar.scrollHeight=900;inspect();
    assert.equal(added[0].removed,true,'reopening removes old fading scrollbar');
    sidebar.scrollHeight=600;inspect();
    added[1].listeners.animationend();
    assert.equal(added[1].removed,true);
    sidebar.scrollHeight=900;inspect();
    window.matchMedia=()=>({matches:true});
    sidebar.scrollHeight=600;inspect();
    assert.equal(added.length,2,'reduced motion keeps native immediate disappearance');
    motion.initSidebarScrollbarExit();assert.equal(observations,3,'initialization must be idempotent');
}
checkPanels().then(checkThemes).then(checkScrollbarExit).then(() => console.log('Frontend motion smoke passed')).catch(error => { console.error(error); process.exitCode = 1; });

// The policy has only three durations and is recalculated from visible content.
assert.deepEqual([0,500,501,850,851,2000].map(motion.disclosureDuration),[220,220,350,350,490,490]);
reduce = false;
window.matchMedia = () => ({matches: reduce});
let contentHeight = 750, measuring = false;
const disclosure = {
    open: false, style: {setProperty(key,value) {this[key]=value;}},
    classList: {add(){measuring=true;},remove(){measuring=false;}},
    children: [{getBoundingClientRect:()=>({top:0,bottom:contentHeight})}],
    querySelector:()=>({})
};
window.getComputedStyle=()=>({display:'block'});
motion.prepareDisclosure(disclosure);
assert.equal(disclosure.style['--disclosure-duration'],'350ms');
assert.equal(disclosure.open,false);
assert.equal(measuring,false);
disclosure.open=true;
motion.prepareDisclosure(disclosure);
assert.equal(disclosure.style['--disclosure-duration'],'350ms','same full height gives same closing tier');
contentHeight=970;
motion.prepareDisclosure(disclosure);
assert.equal(disclosure.style['--disclosure-duration'],'490ms','changed content updates the tier');
contentHeight=250;
motion.prepareDisclosure(disclosure);
assert.equal(disclosure.style['--disclosure-duration'],'220ms');
reduce=true;
disclosure.style['--disclosure-duration']='unchanged';
motion.prepareDisclosure(disclosure);
assert.equal(disclosure.style['--disclosure-duration'],'unchanged');
console.log('Three duration tiers, live content measurement, native state and reduced motion passed');
