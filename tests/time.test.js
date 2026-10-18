const test = require('node:test');
const assert = require('node:assert/strict');
const {timeParts,dayKey,eventOnDay,uniqueEvents}=require('../web/time-utils.js');

test('New York follows spring and autumn DST; Shanghai stays UTC+8',()=>{
  assert.equal(timeParts('America/New_York',new Date('2026-03-08T06:59:00Z')).hour,'01');
  assert.equal(timeParts('America/New_York',new Date('2026-03-08T07:00:00Z')).hour,'03');
  assert.equal(timeParts('America/New_York',new Date('2026-11-01T06:00:00Z')).hour,'01');
  assert.equal(timeParts('Asia/Shanghai',new Date('2026-03-08T07:00:00Z')).hour,'15');
});
test('dayKey defaults to New York independently of the host timezone',()=>{
  assert.equal(dayKey(new Date('2026-10-18T02:00:00Z')),'2026-10-17');
  assert.equal(dayKey(new Date('2026-10-18T04:00:00Z')),'2026-10-18');
});
test('Google all-day end is exclusive',()=>{
  const e={start:{date:'2026-10-18'},end:{date:'2026-10-20'}};
  assert.equal(eventOnDay(e,'2026-10-18'),true);
  assert.equal(eventOnDay(e,'2026-10-19'),true);
  assert.equal(eventOnDay(e,'2026-10-20'),false);
});
test('overnight meeting appears on both dates, midnight end excluded',()=>{
  const e={start:{dateTime:'2026-10-18T23:00:00-04:00'},end:{dateTime:'2026-10-19T01:00:00-04:00'}};
  assert.equal(eventOnDay(e,'2026-10-18'),true);assert.equal(eventOnDay(e,'2026-10-19'),true);
  e.end.dateTime='2026-10-19T00:00:00-04:00';assert.equal(eventOnDay(e,'2026-10-19'),false);
});
test('same shared event across accounts appears once; recurring instances survive',()=>{
  const rows=[{id:'a',uid:'shared',start:{date:'2026-10-18'}},{id:'b',uid:'shared',start:{date:'2026-10-18'}},{id:'c',uid:'shared',start:{date:'2026-10-19'}}];
  assert.equal(uniqueEvents(rows).length,2);
});
test('Shanghai date changes at UTC 16:00 while dayKey still defaults to New York',()=>{
  const before=new Date('2026-10-18T15:59:59.999Z'), midnight=new Date('2026-10-18T16:00:00Z');
  assert.equal(dayKey(before,'Asia/Shanghai'),'2026-10-18');
  assert.equal(dayKey(midnight,'Asia/Shanghai'),'2026-10-19');
  assert.equal(dayKey(before),'2026-10-18');
  assert.equal(dayKey(midnight),'2026-10-18');
});
test('an explicit Shanghai event date does not change the default New York event date',()=>{
  const e={start:{dateTime:'2026-10-18T16:30:00Z'},end:{dateTime:'2026-10-18T17:30:00Z'}};
  assert.equal(eventOnDay(e,'2026-10-18','Asia/Shanghai'),false);
  assert.equal(eventOnDay(e,'2026-10-19','Asia/Shanghai'),true);
  assert.equal(eventOnDay(e,'2026-10-18'),true);
  assert.equal(eventOnDay(e,'2026-10-19'),false);
});
test('an event crossing Shanghai midnight appears on both local dates',()=>{
  const e={start:{dateTime:'2026-10-18T23:00:00+08:00'},end:{dateTime:'2026-10-19T01:00:00+08:00'}};
  assert.equal(eventOnDay(e,'2026-10-17','Asia/Shanghai'),false);
  assert.equal(eventOnDay(e,'2026-10-18','Asia/Shanghai'),true);
  assert.equal(eventOnDay(e,'2026-10-19','Asia/Shanghai'),true);
  assert.equal(eventOnDay(e,'2026-10-20','Asia/Shanghai'),false);
});
test('an event ending exactly at Shanghai midnight is excluded from the next date',()=>{
  const e={start:{dateTime:'2026-10-18T23:00:00+08:00'},end:{dateTime:'2026-10-19T00:00:00+08:00'}};
  assert.equal(eventOnDay(e,'2026-10-18','Asia/Shanghai'),true);
  assert.equal(eventOnDay(e,'2026-10-19','Asia/Shanghai'),false);
});
test('Google all-day dates and exclusive end are preserved with an explicit Shanghai zone',()=>{
  const e={start:{date:'2026-10-18'},end:{date:'2026-10-20'}};
  for(const zone of ['America/New_York','Asia/Shanghai']){
    assert.equal(eventOnDay(e,'2026-10-17',zone),false);
    assert.equal(eventOnDay(e,'2026-10-18',zone),true);
    assert.equal(eventOnDay(e,'2026-10-19',zone),true);
    assert.equal(eventOnDay(e,'2026-10-20',zone),false);
  }
});
