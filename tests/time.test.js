const test = require('node:test');
const assert = require('node:assert/strict');
const {timeParts,dayKey,eventOnDay,uniqueEvents}=require('../web/time-utils.js');

test('New York follows spring and autumn DST; Shanghai stays UTC+8',()=>{
  assert.equal(timeParts('America/New_York',new Date('2026-03-08T06:59:00Z')).hour,'01');
  assert.equal(timeParts('America/New_York',new Date('2026-03-08T07:00:00Z')).hour,'03');
  assert.equal(timeParts('America/New_York',new Date('2026-11-01T06:00:00Z')).hour,'01');
  assert.equal(timeParts('Asia/Shanghai',new Date('2026-03-08T07:00:00Z')).hour,'15');
});
test('calendar date is New York date, independent of Mac timezone',()=>{
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
