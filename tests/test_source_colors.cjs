'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {normalizeHex,parseSourceColors,hsvToHex,hexToHsv}=require('../skills/codex-skill-atlas/app/web/source-colors.js');

test('accepts and normalizes supported HEX values; rejects invalid CSS',()=>{
  assert.equal(normalizeHex('#AbC'),'#aabbcc');
  assert.equal(normalizeHex(' 568AAF '),'#568aaf');
  for(const value of ['',null,'#12','#abcd','#11223344','red','var(--x)','url(x)','<script>'])assert.equal(normalizeHex(value),null);
});
test('color preferences merge valid sources with defaults and ignore unknown keys',()=>{
  const defaults={builtin:'#568aaf',local:'#49a580',plugin:'#9485b8'};
  assert.deepEqual(parseSourceColors('{"builtin":"#abc","local":"invalid","__proto__":{"polluted":true},"other":"#fff"}',defaults),{builtin:'#aabbcc',local:'#49a580',plugin:'#9485b8'});
  for(const raw of [null,'not json','[]','"text"','null'])assert.deepEqual(parseSourceColors(raw,defaults),defaults);
  assert.deepEqual(defaults,{builtin:'#568aaf',local:'#49a580',plugin:'#9485b8'});
});
test('HSV palette handles primary hues, hue wrap, black and white',()=>{
  for(const [h,hex] of [[0,'#ff0000'],[60,'#ffff00'],[120,'#00ff00'],[180,'#00ffff'],[240,'#0000ff'],[300,'#ff00ff'],[360,'#ff0000'],[-60,'#ff00ff']])assert.equal(hsvToHex({h,s:1,v:1}),hex);
  assert.equal(hsvToHex({h:200,s:0,v:1}),'#ffffff');
  assert.equal(hsvToHex({h:200,s:1,v:0}),'#000000');
  assert.equal(hsvToHex({h:0,s:2,v:2}),'#ff0000');
});
test('HEX to HSV to HEX keeps all palette and custom colors unchanged',()=>{
  for(const hex of ['#568aaf','#49a580','#bc995e','#9485b8','#bc7b88','#123456','#ffffff','#000000','#fefefe','#ff0000','#000001','#b0ffee'])assert.equal(hsvToHex(hexToHsv(hex)),hex);
});
test('achromatic colors preserve the chosen hue for further editing',()=>{
  assert.deepEqual(hexToHsv('#ffffff',270),{h:270,s:0,v:1});
  assert.deepEqual(hexToHsv('#000000',135),{h:135,s:0,v:0});
});
