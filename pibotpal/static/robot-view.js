import * as THREE from './vendor/three.module.min.js';
import { OrbitControls } from './vendor/OrbitControls.js';

// Controller coordinates: +x right, +y forward, +z up, millimeters.
const point = ([x,y,z]) => new THREE.Vector3(x,z,-y);
const sign = [[1,1],[-1,1],[-1,-1],[1,-1]];
const material = (color,options={}) => new THREE.MeshStandardMaterial({color,roughness:.55,metalness:.35,...options});

export class RobotView {
  constructor(container) {
    this.container=container;
    this.scene=new THREE.Scene();
    this.scene.fog=new THREE.FogExp2(0x121c23,.0008);
    this.camera=new THREE.PerspectiveCamera(38,1,1,10000);
    this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio,1.75));
    this.renderer.setClearColor(0,0);
    this.renderer.outputColorSpace=THREE.SRGBColorSpace;
    this.container.prepend(this.renderer.domElement);
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);
    this.controls.enableDamping=true;
    this.controls.minDistance=140;
    this.controls.maxDistance=6000;
    this.controls.maxPolarAngle=Math.PI*.88;
    this.controls.target.set(0,-12,0);
    this.reset();
    this.scene.add(new THREE.HemisphereLight(0xc9e6fa,0x263b30,2.8));
    const sun=new THREE.DirectionalLight(0xe7f7ff,3);sun.position.set(160,240,160);this.scene.add(sun);
    const warm=new THREE.DirectionalLight(0xffb672,.8);warm.position.set(-100,50,-150);this.scene.add(warm);
    const grid=new THREE.GridHelper(1400,56,0x39534e,0x24343e);grid.position.y=-100;grid.material.transparent=true;grid.material.opacity=.45;this.scene.add(grid);
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(3000,3000),material(0x101920,{transparent:true,opacity:.35}));floor.rotation.x=-Math.PI/2;floor.position.y=-100.5;this.scene.add(floor);
    this.body=new THREE.Group();this.scene.add(this.body);
    this.buildBody();
    this.legGroups=[];
    const mats={link:material(0x8496a0),foot:material(0x313d46),servo:material(0x142736),joint:material(0x6ddfc2,{metalness:.55,roughness:.3}),ghost:material(0x576c75,{transparent:true,opacity:.3})};
    this.mats=mats;
    for(let i=0;i<4;i++){
      const group=new THREE.Group();this.scene.add(group);
      const links=[0,1,2].map((n)=>{const mesh=new THREE.Mesh(new THREE.CylinderGeometry(n===2?5:6,n===2?3:6,1,10),mats.ghost);group.add(mesh);return mesh;});
      const joints=[0,1,2,3].map((n)=>{const mesh=new THREE.Mesh(n===3?new THREE.SphereGeometry(6,12,8):new THREE.BoxGeometry(13,16,20),mats.ghost);group.add(mesh);return mesh;});
      const label=this.makeLabel(`0${i+1}`);group.add(label);
      this.legGroups.push({group,links,joints,label});
    }
    this.range=new THREE.Group();this.scene.add(this.range);
    this.rangeLine=new THREE.Line(new THREE.BufferGeometry(),new THREE.LineDashedMaterial({color:0xf0ac72,dashSize:8,gapSize:5,transparent:true,opacity:.8}));this.range.add(this.rangeLine);
    this.rangeDot=new THREE.Mesh(new THREE.SphereGeometry(7,16,10),material(0xf0ac72,{emissive:0x75461a}));this.range.add(this.rangeDot);
    this.rangeLabel=this.makeLabel('');this.range.add(this.rangeLabel);
    this.range.visible=false;
    this.update({legs:[]});
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(container);this.resize();
    this.renderer.setAnimationLoop(()=>{if(!document.hidden && this.container.offsetWidth){this.controls.update();this.renderer.render(this.scene,this.camera);}});
  }
  reset(){this.camera.position.set(255,220,-295);this.controls.target.set(0,-12,0);this.controls.update();}
  resize(){const w=this.container.clientWidth,h=this.container.clientHeight;if(!w||!h)return;this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.renderer.setSize(w,h,false);}
  box(parent,dimensions,position,color){const m=new THREE.Mesh(new THREE.BoxGeometry(...dimensions),material(color));m.position.copy(point(position));parent.add(m);return m;}
  buildBody(){
    this.box(this.body,[88,5,88],[0,0,0],0xb83a39);
    this.box(this.body,[86,4,86],[0,0,20],0x8f292b);
    this.box(this.body,[58,4,64],[0,0,27],0x267754);
    this.box(this.body,[24,7,24],[0,0,33],0x283237);
    this.box(this.body,[16,12,18],[20,-22,35],0xaab4b9);
    this.box(this.body,[16,12,18],[0,-22,35],0xaab4b9);
    this.box(this.body,[36,15,52],[0,0,-10],0x17242e);
    for(const x of [-31,31])for(const y of [-31,31])this.box(this.body,[4,20,4],[x,y,10],0xa48943);
    // Front-facing ultrasonic board and its two transducers.
    this.box(this.body,[49,19,3],[0,48,13],0x215177);
    for(const x of [-14,14]){const rim=new THREE.Mesh(new THREE.CylinderGeometry(8,8,10,24),material(0xb1c0c6));rim.rotation.x=Math.PI/2;rim.position.copy(point([x,54,13]));this.body.add(rim);const eye=new THREE.Mesh(new THREE.CylinderGeometry(5.9,5.9,.8,20),material(0x243640));eye.rotation.x=Math.PI/2;eye.position.copy(point([x,59.5,13]));this.body.add(eye);}
    this.box(this.body,[25,23,3],[0,39,39],0x245c45);
    const lens=new THREE.Mesh(new THREE.CylinderGeometry(6,6,9,24),material(0x141c24));lens.rotation.x=Math.PI/2;lens.position.copy(point([0,45,39]));this.body.add(lens);
    const glass=new THREE.Mesh(new THREE.CircleGeometry(4.5,24),material(0x67b7ca,{emissive:0x12303c,roughness:.1}));glass.rotation.y=Math.PI;glass.position.copy(point([0,50,39]));this.body.add(glass);
    // Microphone and speaker locations are schematic, not surveyed mount coordinates.
    this.box(this.body,[9,4,16],[31,-10,33],0x22313c);
    this.microphoneShell=new THREE.Mesh(new THREE.CylinderGeometry(4.5,4.5,16,16),material(0x647b86));
    this.microphoneShell.position.copy(point([31,-10,43]));this.body.add(this.microphoneShell);
    for(const h of [40,43,46]){const ring=new THREE.Mesh(new THREE.TorusGeometry(4.6,.5,5,16),material(0x25353e));ring.rotation.x=Math.PI/2;ring.position.copy(point([31,-10,h]));this.body.add(ring);}
    this.microphoneLight=new THREE.Mesh(new THREE.SphereGeometry(2.5,12,8),material(0x64717b,{emissive:0x000000}));
    this.microphoneLight.position.copy(point([31,-10,53]));this.body.add(this.microphoneLight);
    this.microphoneLabel=this.makeLabel('MIC MUTED');this.microphoneLabel.position.copy(point([48,-10,64]));this.microphoneLabel.scale.set(58,14.5,1);this.body.add(this.microphoneLabel);
    this.speakerShell=new THREE.Mesh(new THREE.CylinderGeometry(13,13,6,24),material(0x273b48));this.speakerShell.rotation.x=Math.PI/2;this.speakerShell.position.copy(point([0,-48,5]));this.body.add(this.speakerShell);
    this.speakerCone=new THREE.Mesh(new THREE.CylinderGeometry(10,10,1,24),material(0x526573,{emissive:0x000000}));this.speakerCone.rotation.x=Math.PI/2;this.speakerCone.position.copy(point([0,-51.5,5]));this.body.add(this.speakerCone);
    for(const radius of [4,7,10]){const ring=new THREE.Mesh(new THREE.TorusGeometry(radius,.45,5,24),material(0x182832));ring.position.copy(point([0,-52.1,5]));this.body.add(ring);}
    this.speakerLabel=this.makeLabel('SPEAKER IDLE');this.speakerLabel.position.copy(point([0,-64,19]));this.speakerLabel.scale.set(64,16,1);this.body.add(this.speakerLabel);
    const front=this.makeLabel('FRONT');front.position.copy(point([0,95,-75]));front.scale.set(54,18,1);this.body.add(front);
    const positions=[[0,51,39],[-70,210,80],[70,210,80],[0,51,39],[-70,210,-3],[70,210,-3],[0,51,39],[-70,210,80],[-70,210,-3],[70,210,-3],[70,210,80]];
    const geometry=new THREE.BufferGeometry().setFromPoints(positions.map(point));
    this.cameraGuide=new THREE.Line(geometry,new THREE.LineBasicMaterial({color:0x6ddfc2,transparent:true,opacity:.11}));this.body.add(this.cameraGuide);
  }
  makeLabel(text){const canvas=document.createElement('canvas');canvas.width=256;canvas.height=64;const texture=new THREE.CanvasTexture(canvas);const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false,opacity:.85}));sprite.scale.set(48,12,1);sprite.userData={canvas,texture};this.labelText(sprite,text);return sprite;}
  labelText(sprite,text){const {canvas,texture}=sprite.userData,ctx=canvas.getContext('2d');ctx.clearRect(0,0,256,64);ctx.font='24px monospace';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#a4c1c5';ctx.fillText(text,128,32);texture.needsUpdate=true;}
  neutral(i){const [sx,sy]=sign[i],hip=[sx*38.5,sy*38.5,0],r=Math.SQRT1_2;return [hip,[hip[0]+sx*33*r,hip[1]+sy*33*r,0],[hip[0]+sx*64*r,hip[1]+sy*64*r,36],[hip[0]+sx*104*r,hip[1]+sy*104*r,-31]];}
  update(status){
    for(let i=0;i<4;i++){
      const leg=status.legs?.[i],known=Array.isArray(leg?.joints_mm)&&leg.joints_mm.length===4;
      const points=(known?leg.joints_mm:this.neutral(i)).map(point),g=this.legGroups[i];
      for(let j=0;j<3;j++){const a=points[j],b=points[j+1],v=b.clone().sub(a);g.links[j].position.copy(a).add(b).multiplyScalar(.5);g.links[j].scale.y=v.length();g.links[j].quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),v.normalize());g.links[j].material=known?this.mats.link:this.mats.ghost;}
      for(let j=0;j<4;j++){g.joints[j].position.copy(points[j]);g.joints[j].material=known?(j===3?this.mats.foot:this.mats.joint):this.mats.ghost;}
      g.label.position.copy(points[3]).add(new THREE.Vector3(sign[i][0]*12,12,-sign[i][1]*12));
    }
    const u=status.ultrasonic;this.range.visible=Boolean(u?.valid&&Number.isFinite(u.distance_cm));
    if(this.range.visible){const origin=point([0,60,13]),end=point([0,60+u.distance_cm*10,13]);this.rangeLine.geometry.dispose();this.rangeLine.geometry=new THREE.BufferGeometry().setFromPoints([origin,end]);this.rangeLine.computeLineDistances();this.rangeDot.position.copy(end);this.rangeLabel.position.copy(end).add(new THREE.Vector3(0,14,0));this.rangeLabel.scale.set(90,18,1);this.labelText(this.rangeLabel,`${u.distance_cm.toFixed(1)} cm`);}
    this.cameraGuide.visible=Boolean(status.camera?.enabled);
    const listening=Boolean(status.microphone?.enabled),speaking=Boolean(status.speaker?.busy);
    this.microphoneLight.material.color.setHex(listening?0x6ddfc2:0x64717b);
    this.microphoneLight.material.emissive.setHex(listening?0x2c8c71:0x000000);
    this.speakerCone.material.color.setHex(speaking?0xf0ac72:0x526573);
    this.speakerCone.material.emissive.setHex(speaking?0x75461a:0x000000);
    if(this.microphoneLabel.userData.state!==listening){this.labelText(this.microphoneLabel,listening?'MIC ACTIVE':'MIC MUTED');this.microphoneLabel.userData.state=listening;}
    if(this.speakerLabel.userData.state!==speaking){this.labelText(this.speakerLabel,speaking?'SPEAKER ACTIVE':'SPEAKER IDLE');this.speakerLabel.userData.state=speaking;}
  }
}
