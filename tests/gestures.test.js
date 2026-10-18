const test = require('node:test');
const assert = require('node:assert/strict');
const {PageSwipe,quotaTone} = require('../web/gestures.js');
test('a long decaying momentum tail, including reversals, turns exactly one page',()=>{
  const g=new PageSwipe();let steps=[];
  for(let t=0;t<2400;t+=20){const r=g.wheel(t<180?18:t<2000?3:-2,0,t);if(r.step)steps.push(r.step);}
  assert.deepEqual(steps,[1]);
  assert.equal(g.wheel(-80,0,2800).step,-1);
});
test('vertical intent stays vertical even if momentum drifts sideways',()=>{
  const g=new PageSwipe();g.wheel(2,24,0);
  for(let t=20;t<1000;t+=20)assert.equal(g.wheel(18,3,t).step,0);
  assert.equal(g.wheel(85,0,1400).step,1);
});
test('tiny packets sustain the latch; new streams and line-mode input work',()=>{
  const g=new PageSwipe();assert.equal(g.wheel(90,0,0).step,1);
  for(let t=100;t<1800;t+=100)assert.equal(g.wheel(0.1,0,t).step,0);
  assert.equal(g.wheel(8,0,2200,1).step,1);
  assert.equal(g.wheel(8,0,2250,1).step,0);
});
test('quota colors are used-percent thresholds, including unavailable values',()=>{
  assert.deepEqual([null,0,69,70,84,85,94,95,100].map(quotaTone),['unknown','green','green','yellow','yellow','orange','orange','red','red']);
});
