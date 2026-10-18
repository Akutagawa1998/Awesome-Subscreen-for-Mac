(function(root) {
  'use strict';
  function timeParts(zone, date = new Date()) {
    return Object.fromEntries(new Intl.DateTimeFormat('en-GB', {timeZone:zone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23',timeZoneName:'short'}).formatToParts(date).map(x=>[x.type,x.value]));
  }
  function dayKey(date, zone='America/New_York') {
    const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date).map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  }
  function eventOnDay(e, key, zone='America/New_York') {
    if(e.start.date) return key>=e.start.date && key<(e.end.date || e.start.date+'~');
    const start=new Date(e.start.dateTime), end=new Date(e.end.dateTime || e.start.dateTime);
    if(!Number.isFinite(+start) || !Number.isFinite(+end)) return false;
    return key>=dayKey(start,zone) && key<=dayKey(new Date(Math.max(+start,+end-1)),zone);
  }
  function uniqueEvents(events) {
    const seen=new Set();
    return events.filter(e=>{const key=(e.uid||e.id)+'|'+(e.start.dateTime||e.start.date);if(seen.has(key))return false;seen.add(key);return true;});
  }
  const helpers={timeParts,dayKey,eventOnDay,uniqueEvents};
  if(typeof module!=='undefined' && module.exports)module.exports=helpers;
  else root.timeHelpers=helpers;
})(typeof window!=='undefined'?window:globalThis);
