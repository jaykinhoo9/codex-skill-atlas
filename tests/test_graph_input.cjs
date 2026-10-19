'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {GraphInput}=require('../skills/codex-skill-atlas/app/web/graph-input.js');

function setup({scale=1,pan={x:0,y:0},selected=[]}={}){
  const nodes=[{id:'a',x:20,y:20},{id:'b',x:50,y:30},{id:'c',x:150,y:100}];
  const state={nodes,view:{scale,pan},selected:new Set(selected),box:null,clicked:[],blank:0,committed:0,finished:0,panWrites:0};
  const input=new GraphInput({
    getNodes:()=>nodes,getView:()=>state.view,getSelection:()=>state.selected,
    setSelection:ids=>state.selected=ids,setPan:pan=>{state.view.pan=pan;state.panWrites++;},
    moveNodes:positions=>positions.forEach(p=>Object.assign(nodes.find(n=>n.id===p.id),p)),
    showBox:box=>state.box=box,updateCursor:()=>{},
    onClickNode:n=>state.clicked.push(n.id),onClickBlank:()=>state.blank++,
    onSelectBox:()=>state.committed++,onFinish:()=>state.finished++
  });
  const down=(x,y,node=null,additive=false)=>input.down({x,y},node,{pointerId:1,additive});
  const move=(x,y)=>input.move({x,y},1),up=(x,y)=>input.up({x,y},1);
  return{state,input,down,move,up};
}

test('ordinary blank drag selects without moving the camera; reverse drag respects zoom',()=>{
  const {state,down,move,up}=setup({scale:2,pan:{x:100,y:50}});
  down(215,125);move(110,60);
  assert.deepEqual([...state.selected],['a','b']);
  assert.deepEqual(state.box,{x:110,y:60,width:105,height:65});
  up(110,60);
  assert.equal(state.panWrites,0);assert.equal(state.committed,1);assert.equal(state.box,null);
});

test('holding Space pans even when the gesture begins on a selected node',()=>{
  const {state,input,down,move,up}=setup({selected:['a','b']});
  input.setSpace(true);assert.equal(input.cursor(true),'grab');
  down(20,20,state.nodes[0]);assert.equal(input.cursor(),'grabbing');
  move(100,60);up(100,60);
  assert.deepEqual(state.view.pan,{x:80,y:40});
  assert.deepEqual(state.nodes[0],{id:'a',x:20,y:20});
  assert.deepEqual([...state.selected],['a','b']);assert.equal(state.clicked.length,0);
  assert.equal(input.cursor(),'grab');input.setSpace(false);assert.equal(input.cursor(),'default');
});

test('releasing Space mid-drag stops panning immediately and the next drag selects',()=>{
  const {state,input,down,move,up}=setup();
  input.setSpace(true);down(0,0);move(10,10);input.setSpace(false);
  move(200,200);up(200,200);
  assert.deepEqual(state.view.pan,{x:10,y:10});assert.equal(state.blank,0);
  down(0,0);move(80,60);up(80,60);
  assert.deepEqual([...state.selected],['a','b']);assert.equal(state.panWrites,1);
});

test('dragging a member moves the whole selection in world coordinates',()=>{
  const {state,down,move,up}=setup({scale:2,selected:['a','b']});
  down(40,40,state.nodes[0]);move(80,60);up(80,60);
  assert.deepEqual(state.nodes,[{id:'a',x:40,y:30},{id:'b',x:70,y:40},{id:'c',x:150,y:100}]);
  assert.equal(state.panWrites,0);assert.equal(state.clicked.length,0);
});

test('dragging a node outside the selection moves only that node',()=>{
  const {state,down,up}=setup({selected:['a','b']});
  down(150,100,state.nodes[2]);up(170,130);
  assert.deepEqual([...state.selected],[]);
  assert.equal(state.nodes[0].x,20);assert.equal(state.nodes[2].x,170);assert.equal(state.panWrites,0);
});

test('Shift appends to the original selection; cancelling restores it',()=>{
  const {state,input,down,move,up}=setup({selected:['c']});
  down(0,0,null,true);move(80,60);assert.deepEqual([...state.selected],['c','a','b']);
  input.cancel();assert.deepEqual([...state.selected],['c']);assert.equal(state.box,null);
  down(0,0,null,true);up(80,60);assert.deepEqual([...state.selected],['c','a','b']);
});

test('pressing Space during box selection restores it and pans from the current pointer without a jump',()=>{
  const {state,input,down,move,up}=setup({selected:['c']});
  down(0,0);move(80,60);input.setSpace(true);
  assert.deepEqual([...state.selected],['c']);assert.equal(state.box,null);
  move(100,90);up(100,90);assert.deepEqual(state.view.pan,{x:20,y:30});assert.equal(state.committed,0);
});

test('window blur/pointer cancellation clears hand mode and ignores subsequent movement',()=>{
  const {state,input,down,move,up}=setup();
  input.setSpace(true);down(0,0);move(10,10);input.cancel();
  assert.equal(input.spaceHeld,false);assert.equal(input.gesture,null);assert.equal(input.cursor(),'default');
  move(100,100);up(100,100);assert.equal(state.panWrites,1);
});

test('clicks keep details and blank-click actions; tiny pointer jitter is not a drag',()=>{
  const {state,input,down,up}=setup();
  down(20,20,state.nodes[0]);up(22,21);assert.deepEqual(state.clicked,['a']);
  down(0,0);up(1,2);assert.equal(state.blank,1);assert.equal(state.panWrites,0);
  input.setSpace(true);down(0,0);up(0,0);assert.equal(state.blank,1);
});

test('right button and unrelated pointers cannot start or alter a gesture',()=>{
  const {state,input,down,move,up}=setup();
  assert.equal(input.down({x:0,y:0},null,{pointerId:2,button:2}),false);
  down(0,0);input.move({x:100,y:100},2);input.up({x:100,y:100},2);
  assert.equal(input.gesture.moved,false);move(80,60);up(80,60);assert.deepEqual([...state.selected],['a','b']);
});

test('zoom can finish a gesture without losing the currently held Space key',()=>{
  const {input,down,move}=setup();
  input.setSpace(true);down(0,0);move(10,10);input.cancel({releaseSpace:false});
  assert.equal(input.gesture,null);assert.equal(input.cursor(),'grab');
  input.setSpace(false);assert.equal(input.cursor(),'default');
});

test('a forgiving click hit outside the visible session circle starts a marquee, not a node drag',()=>{
  const {state,input,move,up}=setup();
  input.down({x:9,y:20},state.nodes[0],{pointerId:1,dragNode:null});
  move(80,60);up(80,60);
  assert.equal(state.committed,1);assert.deepEqual([...state.selected],['a','b']);
  assert.deepEqual(state.nodes[0],{id:'a',x:20,y:20});assert.equal(state.panWrites,0);
});

test('a click in the forgiving hit area still opens node details',()=>{
  const {state,input,up}=setup();
  input.down({x:9,y:20},state.nodes[0],{pointerId:1,dragNode:null});up(9,20);
  assert.deepEqual(state.clicked,['a']);assert.equal(state.committed,0);
});

test('marquee selects the visible circle edge even when the center is outside',()=>{
  const {state,down,up}=setup();state.nodes[0].r=8;
  down(25,10);up(36,30);assert.deepEqual([...state.selected],['a']);
  // A corner outside the actual circle must not count as touching it.
  down(27,27);up(40,40);assert.deepEqual([...state.selected],[]);
  down(36,30);up(25,10);assert.deepEqual([...state.selected],['a']);
});

test('minimum rendered session radius is used when zoomed out',()=>{
  const {state,input,down,up}=setup({scale:.2,pan:{x:100,y:100}});
  input.options.getNodeGeometry=n=>({x:n.x*.2+100,y:n.y*.2+100,radius:n.id==='c'?8:3});
  // Session c is centered at (130,120); its visible edge reaches x=138.
  down(136,115);up(145,125);assert.deepEqual([...state.selected],['c']);
});

test('Shift can start a marquee directly on a session without moving it',()=>{
  const {state,down,up}=setup({selected:['c']});
  down(20,20,state.nodes[0],true);up(80,60);
  assert.deepEqual([...state.selected],['c','a','b']);assert.equal(state.committed,1);
  assert.deepEqual(state.nodes[0],{id:'a',x:20,y:20});assert.equal(state.panWrites,0);
});

test('mixed skill/session geometry remains the pointerdown snapshot during live updates',()=>{
  const {state,down,move,up}=setup();
  state.nodes[0].type='session';state.nodes[1].type='skill';
  down(0,0);move(80,60);assert.deepEqual([...state.selected],['a','b']);
  state.nodes[0].x=300;state.nodes[1].y=300;
  state.nodes.push({id:'new-session',type:'session',x:40,y:30});
  up(80,60);assert.deepEqual([...state.selected],['a','b']);assert.equal(state.committed,1);
});
