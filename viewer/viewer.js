/* Tiny local WebGL viewer. No npm, CDN, network assets, or runtime dependencies. */
class MeshViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = canvas.getContext('webgl', {antialias: true});
    if (!this.gl) throw new Error('WebGL is unavailable. Enable browser hardware acceleration, or download the OBJ/GLB.');
    const gl = this.gl;
    const compile = (type, source) => {
      const shader = gl.createShader(type); gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    this.program = gl.createProgram();
    gl.attachShader(this.program, compile(gl.VERTEX_SHADER, `
      attribute vec3 position; attribute vec3 normal;
      uniform float yaw; uniform float pitch; uniform float zoom; uniform float aspect;
      varying vec3 n;
      vec3 rotate(vec3 p) {
        float c=cos(yaw),s=sin(yaw),a=cos(pitch),b=sin(pitch);
        vec3 q=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);
        return vec3(q.x,a*q.y-b*q.z,b*q.y+a*q.z);
      }
      void main(){ vec3 p=rotate(position); n=rotate(normal);
        gl_Position=vec4(p.x*zoom/aspect,p.y*zoom,-p.z*0.25,1.0); }
    `));
    gl.attachShader(this.program, compile(gl.FRAGMENT_SHADER, `
      precision mediump float; varying vec3 n;
      void main(){float light=.45+.55*max(0.,dot(normalize(n),normalize(vec3(-.5,.8,1.))));
        gl_FragColor=vec4(vec3(.12,.57,.56)*light,1.);}
    `));
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(this.program));
    this.buffers = [];
    this.count = 0;
    this.reset();
    let pointer = null;
    canvas.addEventListener('pointerdown', e => {pointer = [e.clientX, e.clientY]; canvas.setPointerCapture(e.pointerId); canvas.focus();});
    canvas.addEventListener('pointermove', e => {
      if (!pointer) return;
      this.yaw += (e.clientX-pointer[0])*.009;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch+(e.clientY-pointer[1])*.009));
      pointer = [e.clientX, e.clientY]; this.draw();
    });
    for (const event of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(event, () => pointer = null);
    canvas.addEventListener('wheel', e => {e.preventDefault(); this.zoom = Math.max(.3,Math.min(3,this.zoom*Math.exp(-e.deltaY*.001))); this.draw();}, {passive:false});
    canvas.addEventListener('keydown', e => {
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','r','R'].includes(e.key)) return;
      e.preventDefault();
      if (e.key==='ArrowLeft') this.yaw-=.1;
      if (e.key==='ArrowRight') this.yaw+=.1;
      if (e.key==='ArrowUp') this.pitch=Math.max(-1.5,this.pitch-.1);
      if (e.key==='ArrowDown') this.pitch=Math.min(1.5,this.pitch+.1);
      if (['+','='].includes(e.key)) this.zoom=Math.min(3,this.zoom*1.1);
      if (e.key==='-') this.zoom=Math.max(.3,this.zoom/1.1);
      if (e.key.toLowerCase()==='r') this.reset();
      this.draw();
    });
    new ResizeObserver(() => this.draw()).observe(canvas);
  }
  reset() {this.yaw=-.5; this.pitch=.25; this.zoom=.8; this.draw();}
  clear() {this.count=0; this.draw();}
  load(mesh) {
    const gl=this.gl, positions=new Float32Array(mesh.positions), normals=new Float32Array(mesh.normals);
    if (!positions.length || positions.length%9 || positions.length!==normals.length) throw new Error('Invalid preview mesh');
    let lo=[Infinity,Infinity,Infinity], hi=[-Infinity,-Infinity,-Infinity];
    for(let i=0;i<positions.length;i++) {let k=i%3;lo[k]=Math.min(lo[k],positions[i]);hi[k]=Math.max(hi[k],positions[i]);}
    const scale=Math.max(...hi.map((x,i)=>x-lo[i]))/2;
    for(let i=0;i<positions.length;i++) positions[i]=(positions[i]-(lo[i%3]+hi[i%3])/2)/scale;
    for(const b of this.buffers) gl.deleteBuffer(b);
    this.buffers=[]; gl.useProgram(this.program);
    for(const [name,data] of [['position',positions],['normal',normals]]) {
      const b=gl.createBuffer();this.buffers.push(b);gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);
      const location=gl.getAttribLocation(this.program,name);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0);
    }
    this.count=positions.length/3; this.reset();
  }
  draw() {
    const gl=this.gl;if(!gl||!this.program)return;
    const ratio=Math.min(devicePixelRatio||1,2);
    this.canvas.width=Math.round(this.canvas.clientWidth*ratio);this.canvas.height=Math.round(this.canvas.clientHeight*ratio);
    gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(.914,.933,.91,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);gl.useProgram(this.program);
    for(const [name,value] of Object.entries({yaw:this.yaw,pitch:this.pitch,zoom:this.zoom,aspect:this.canvas.width/Math.max(1,this.canvas.height)})) gl.uniform1f(gl.getUniformLocation(this.program,name),value);
    if(this.count)gl.drawArrays(gl.TRIANGLES,0,this.count);
  }
}
window.MeshViewer=MeshViewer;
