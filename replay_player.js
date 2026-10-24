(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('replay-data').textContent);
  const el = id => document.getElementById(id);
  const metadata = data.metadata || {};
  const times = data.times || [];
  if (!times.length) {
    el('player').textContent = 'Recorded track positions are unavailable for this session.';
    el('player').className = 'error';
    return;
  }
  const duration = times[times.length - 1];
  const race = ['R', 'S'].includes(metadata.session_code);
  const visible = new Set(data.visible_codes || data.drivers.map(driver => driver.code));
  const drivers = data.drivers;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const initialTime = times.find((t,index)=>drivers.some(driver=>visible.has(driver.code)
    && finite(data.cars[driver.code]?.x?.[index]) && finite(data.cars[driver.code]?.y?.[index]))) ?? times[0];
  let selected = drivers.filter(driver => visible.has(driver.code)).slice(0, 1).map(driver => driver.code);
  let current = initialTime, playing = false, previous = null, lastUi = -Infinity;
  let showStops = false, gapMode = 'interval', showLabels = true;
  const canvas = el('track'), ctx = canvas.getContext('2d');
  let width = 600, height = 570;
  const fmt = (value, digits = 0) => finite(value) ? value.toFixed(digits) : 'N/A';
  function clock(seconds) {
    const whole = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(whole / 3600), minutes = Math.floor(whole % 3600 / 60), secs = whole % 60;
    return (hours ? hours + ':' + String(minutes).padStart(2, '0') : String(minutes).padStart(2, '0')) + ':' + String(secs).padStart(2, '0');
  }
  function sampleIndex(t) {
    let low = 0, high = times.length - 1;
    while (low < high) {
      const mid = Math.ceil((low + high) / 2);
      if (times[mid] <= t) low = mid; else high = mid - 1;
    }
    return low;
  }
  function sample(car, field, index) { return car?.[field]?.[index] ?? null; }
  function gap(value, leader = false) {
    if (finite(value)) return value === 0 && leader ? 'LEADER' : '+' + value.toFixed(3);
    return typeof value === 'string' && value ? value : 'N/A';
  }
  function text(tag, content, className) {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  }
  function color(driver) { return /^#[0-9a-f]{6}$/i.test(driver.color) ? driver.color : '#b8c7db'; }
  function tyre(compound) {
    const name = String(compound || 'UNKNOWN').toUpperCase();
    const values = {SOFT:['S','#ff5d6b'],MEDIUM:['M','#ffe171'],HARD:['H','#edf3f8'],INTERMEDIATE:['I','#50e7a0'],WET:['W','#6bb7ff']};
    return [...(values[name] || ['?','#a6b4c8']),name];
  }
  el('sessionTitle').textContent = [metadata.event, metadata.session].filter(Boolean).join(' · ') || 'Session replay';
  el('sessionDetail').textContent = [metadata.year, metadata.location, 'Recorded session'].filter(Boolean).join(' · ');
  el('driverCount').textContent = drivers.length + ' drivers';
  el('duration').textContent = clock(duration);
  el('seek').max = duration;
  el('coverage').textContent = 'Gaps are recorded timing updates. N/A means missing or stale data. Pit stops count recorded pit lane visits.';
  if (!race) {
    el('gapMode').disabled = true;
    el('gapMode').title = 'Race gaps are available for Grand Prix and Sprint sessions';
    el('boardTitle').textContent = 'Session order';
    el('coverage').textContent = 'Practice and qualifying follow the recorded session clock. Session order is the published timing order, not cars in track order. Race gaps do not apply.';
  }
  if (metadata.archived) {
    el('coverage').textContent = [metadata.position_definition, metadata.gap_definition, metadata.pit_definition].filter(Boolean).join(' ');
  }
  const limitations = metadata.limitations || metadata.warnings || [];
  const playbackNote = metadata.archived ? 'Car positions use recorded lap telemetry. Missing recordings leave gaps in playback.' : 'Car positions are approximate and sampled for playback. Gaps and telemetry update at their source frequency.';
  el('sourceNote').textContent = [metadata.source || 'OpenF1', playbackNote, ...limitations].join(' ');
  const rows = new Map();
  for (const driver of drivers) {
    const row = document.createElement('tr');
    const position = text('td', 'N/A');
    const driverCell = document.createElement('td');
    const button = text('button', driver.code, 'driver-button');
    button.style.setProperty('--team', color(driver));
    button.title = driver.name + ' · ' + driver.team;
    button.setAttribute('aria-label', 'Select ' + driver.code + ' telemetry');
    button.addEventListener('click', () => {
      if (selected.includes(driver.code)) selected = selected.filter(code => code !== driver.code);
      else { selected.push(driver.code); if (selected.length > 3) selected.shift(); }
      renderUi(sampleIndex(current)); draw(sampleIndex(current));
    });
    driverCell.append(button);
    const ahead = text('td', 'N/A'), behind = text('td', 'N/A'), strategy = document.createElement('td');
    row.append(position, driverCell, ahead, behind, strategy);
    rows.set(driver.code,{row,position,ahead,behind,strategy,button});
    el('leaderboard').append(row);
  }
  const trackX = data.track?.x || [], trackY = data.track?.y || [];
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  function bounds(x,y){
    if(finite(x)&&finite(y)){minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);}
  }
  if(trackX.length)trackX.forEach((x,i)=>bounds(x,trackY[i]));
  else Object.values(data.cars).forEach(car=>(car.x||[]).forEach((x,i)=>bounds(x,car.y?.[i])));
  if(!finite(minX)){
    el('player').textContent='Recorded track positions are unavailable for this session.';
    el('player').className='error';return;
  }
  let projection = {scale:1,cx:0,cy:0};
  function xy(x,y) {
    return [(x-(minX+maxX)/2)*projection.scale+width/2, height/2-(y-(minY+maxY)/2)*projection.scale];
  }
  function resize() {
    const bounds = canvas.getBoundingClientRect();
    width = bounds.width; height = bounds.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width*dpr); canvas.height = Math.round(height*dpr);
    ctx.setTransform(dpr,0,0,dpr,0,0);
    projection.scale = Math.min((width-100)/Math.max(1,maxX-minX),(height-125)/Math.max(1,maxY-minY));
    draw(sampleIndex(current));
  }
  function path() {
    ctx.beginPath();
    trackX.forEach((x,i) => {const p=xy(x,trackY[i]);i?ctx.lineTo(...p):ctx.moveTo(...p);});
    ctx.closePath();
  }
  function draw(index) {
    ctx.clearRect(0,0,width,height);
    ctx.lineJoin='round'; ctx.lineCap='round';
    if(trackX.length){
      path();ctx.strokeStyle='#526074';ctx.lineWidth=14;ctx.stroke();
      path();ctx.strokeStyle='#111925';ctx.lineWidth=8;ctx.stroke();
      path();ctx.strokeStyle='#6d7d91';ctx.lineWidth=1;ctx.stroke();
      const start = xy(trackX[0],trackY[0]);
      ctx.fillStyle='#edf3f8';ctx.fillRect(start[0]-3,start[1]-5,6,10);
    }
    let seen=0;
    const next=Math.min(index+1,times.length-1);
    const fraction=next===index?0:(current-times[index])/(times[next]-times[index]);
    const drawDrivers=[...drivers].sort((a,b)=>Number(selected.includes(a.code))-Number(selected.includes(b.code)));
    drawDrivers.forEach(driver=>{
      if(!visible.has(driver.code))return;
      const car=data.cars[driver.code];
      let x=sample(car,'x',index),y=sample(car,'y',index);
      if(!finite(x)||!finite(y))return;
      const nx=sample(car,'x',next),ny=sample(car,'y',next);
      if(finite(nx)&&finite(ny)){x+=(nx-x)*fraction;y+=(ny-y)*fraction;}
      const p=xy(x,y),chosen=selected.includes(driver.code);
      seen++;
      ctx.beginPath();ctx.arc(...p,chosen?7:5,0,Math.PI*2);
      ctx.fillStyle=color(driver);ctx.fill();ctx.strokeStyle=chosen?'#fff':'#102030';ctx.lineWidth=chosen?2:1.5;ctx.stroke();
      if(showLabels||chosen){
        ctx.font=(chosen?'700 ':'500 ')+'10px system-ui';ctx.lineWidth=3;ctx.strokeStyle='#0c121f';
        ctx.strokeText(driver.code,p[0]+10,p[1]-8);ctx.fillStyle=chosen?'#fff':color(driver);ctx.fillText(driver.code,p[0]+10,p[1]-8);
      }
    });
    el('mapEmpty').hidden=seen>0;
  }
  function renderCards(index) {
    el('cards').replaceChildren();
    selected.forEach(code=>{
      const driver=drivers.find(item=>item.code===code),car=data.cars[code];
      const card=document.createElement('article');card.className='driver-card';card.style.setProperty('--team',color(driver));
      const name=document.createElement('div');name.className='card-name';name.append(text('strong',code),text('span',driver.name));card.append(name);
      const values=document.createElement('div');values.className='values';
      [['Speed',fmt(sample(car,'speed',index)),'km/h'],['Gear',fmt(sample(car,'gear',index)),''],['Lap',fmt(sample(car,'lap',index)),'']].forEach(([label,value,unit])=>{
        const box=document.createElement('div'),strong=text('strong',value);strong.append(text('small',' '+unit));box.append(text('label',label),strong);values.append(box);
      });card.append(values);
      const pedals=document.createElement('div');pedals.className='pedals';
      ['throttle','brake'].forEach(field=>{
        const value=sample(car,field,index),box=document.createElement('div'),bar=document.createElement('div'),fill=document.createElement('i');
        box.append(text('label',field+' '+(finite(value)?Math.round(value)+'%':'N/A')));bar.className='meter '+field;fill.style.width=Math.max(0,Math.min(100,value||0))+'%';bar.append(fill);box.append(bar);pedals.append(box);
      });card.append(pedals);
      const drs=sample(car,'drs',index),compound=sample(car,'compound',index),age=sample(car,'age',index);
      const detail=text('div',[compound||'Tyre N/A',finite(age)?Math.round(age)+' laps':'Age N/A',finite(drs)?'DRS '+([10,12,14].includes(drs)?'on':'off'):'DRS N/A'].join(' · '),'muted');
      detail.style.marginTop='10px';detail.style.fontSize='10px';card.append(detail);el('cards').append(card);
    });
  }
  function renderUi(index) {
    el('clock').textContent=clock(current);el('elapsed').textContent=clock(current);el('seek').value=current;
    const ordered=[...drivers].sort((a,b)=>{
      const pa=sample(data.cars[a.code],'position',index),pb=sample(data.cars[b.code],'position',index);
      return (finite(pa)?pa:999)-(finite(pb)?pb:999)||drivers.indexOf(a)-drivers.indexOf(b);
    });
    const laps=ordered.map(driver=>sample(data.cars[driver.code],'lap',index)).filter(finite);
    el('lapLabel').textContent=race&&laps.length?'Lap '+Math.max(...laps)+(metadata.total_laps?' / '+metadata.total_laps:''):'Recorded session';
    ordered.forEach(driver=>{
      const car=data.cars[driver.code],nodes=rows.get(driver.code),pos=sample(car,'position',index);
      nodes.position.textContent=fmt(pos);nodes.row.classList.toggle('selected',selected.includes(driver.code));nodes.row.classList.toggle('filtered',!visible.has(driver.code));
      nodes.button.setAttribute('aria-pressed',String(selected.includes(driver.code)));
      nodes.ahead.textContent=race?(pos===1?'LEADER':gap(sample(car,gapMode==='leader'?'leader':'ahead',index))):'N/A';
      nodes.behind.textContent=race&&gapMode==='interval'?gap(sample(car,'behind',index)):' ';
      nodes.behind.hidden=gapMode==='leader';nodes.strategy.replaceChildren();
      if(showStops){
        const count=sample(car,'stops',index),lap=sample(car,'last_stop_lap',index);
        nodes.strategy.textContent=finite(count)?String(count)+(finite(lap)?' · L'+lap:''):'N/A';
      } else {
        const compound=sample(car,'compound',index),age=sample(car,'age',index),[letter,tint,full]=tyre(compound);
        const badge=text('span',letter,'tyre');badge.style.color=tint;badge.title=full;nodes.strategy.append(badge,text('span',finite(age)?String(Math.round(age)):'N/A','age'));
      }
      el('leaderboard').append(nodes.row);
    });
    const messages=(data.race_control||[]).filter(item=>item.t<=current);
    const message=messages[messages.length-1];
    el('raceMessage').textContent=message?clock(message.t)+' · '+message.message:'Recorded session positions';
    el('raceMessage').style.borderLeft=message?.flag==='RED'?'3px solid #ff5d6b':message?.flag==='YELLOW'?'3px solid #ffe171':'3px solid #4ad7b3';
    const weather=(data.weather||[]).filter(item=>item.t<=current).at(-1);
    el('mapCaption').textContent=weather?[
      finite(weather.air_temperature)?'Air '+weather.air_temperature+'°C':null,
      finite(weather.track_temperature)?'Track '+weather.track_temperature+'°C':null,
      weather.rainfall===1?'Rain reported':weather.rainfall===0?'Dry':null
    ].filter(Boolean).join(' · '):'Recorded positions · interpolated playback';
    renderCards(index);
  }
  function seek(t) {current=Math.max(times[0],Math.min(duration,t));renderUi(sampleIndex(current));draw(sampleIndex(current));}
  function setPlaying(value){playing=value;previous=null;el('play').textContent=playing?'Pause':'Play';el('play').setAttribute('aria-label',playing?'Pause replay':'Play replay');}
  el('play').addEventListener('click',()=>{if(current>=duration)seek(initialTime);setPlaying(!playing);});
  el('restart').addEventListener('click',()=>{setPlaying(false);seek(initialTime);});
  el('back').addEventListener('click',()=>seek(current-10));el('forward').addEventListener('click',()=>seek(current+10));
  el('seek').addEventListener('input',event=>seek(Number(event.target.value)));
  el('strategy').addEventListener('click',()=>{showStops=!showStops;el('strategy').textContent=showStops?'Show tyres':'Show pit stops';el('strategy').setAttribute('aria-pressed',String(showStops));el('strategyHeading').textContent=showStops?'Stops · last lap':'Tyre · age';renderUi(sampleIndex(current));});
  el('gapMode').addEventListener('change',event=>{gapMode=event.target.value;el('gapHeading').textContent=gapMode==='leader'?'To leader':'Ahead';el('backHeading').hidden=gapMode==='leader';renderUi(sampleIndex(current));});
  el('labels').addEventListener('change',event=>{showLabels=event.target.checked;draw(sampleIndex(current));});
  document.addEventListener('keydown',event=>{
    if(['INPUT','SELECT'].includes(event.target.tagName))return;
    if(event.code==='Space'){event.preventDefault();setPlaying(!playing);}
    if(event.code==='ArrowLeft'){event.preventDefault();seek(current-10);}
    if(event.code==='ArrowRight'){event.preventDefault();seek(current+10);}
  });
  new ResizeObserver(resize).observe(canvas);
  document.addEventListener('visibilitychange',()=>{previous=null;});
  function animate(now){
    if(playing&&!document.hidden){
      if(previous!==null)current=Math.min(duration,current+Math.min((now-previous)/1000,.25)*Number(el('speed').value));
      const index=sampleIndex(current);draw(index);
      if(now-lastUi>150){renderUi(index);lastUi=now;}
      if(current>=duration){setPlaying(false);renderUi(index);}
    }
    previous=now;requestAnimationFrame(animate);
  }
  resize();renderUi(sampleIndex(current));requestAnimationFrame(animate);
})();
