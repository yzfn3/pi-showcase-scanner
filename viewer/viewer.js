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
      attribute vec3 position; attribute vec3 normal; attribute vec3 color; attribute vec2 uv;
      uniform float yaw; uniform float pitch; uniform float zoom; uniform float aspect; uniform mediump float pointMode;
      varying vec3 n; varying vec3 c; varying vec2 texUV;
      vec3 rotate(vec3 p) {
        float c=cos(yaw),s=sin(yaw),a=cos(pitch),b=sin(pitch);
        vec3 q=vec3(c*p.x+s*p.z,p.y,-s*p.x+c*p.z);
        return vec3(q.x,a*q.y-b*q.z,b*q.y+a*q.z);
      }
      void main(){ c=color; texUV=uv; vec3 p=rotate(position); n=pointMode>.5?normal:rotate(normal); gl_PointSize=3.0;
        gl_Position=vec4(p.x*zoom/aspect,p.y*zoom,-p.z*0.25,1.0); }
    `));
    gl.attachShader(this.program, compile(gl.FRAGMENT_SHADER, `
      precision mediump float; varying vec3 n; varying vec3 c; varying vec2 texUV; uniform float pointMode; uniform float surfaceStyle; uniform float textureMode; uniform sampler2D photoTexture;
      void main(){float light=.35+.65*abs(dot(normalize(n),normalize(vec3(-.5,.8,1.))));
        vec3 base=textureMode>.5?texture2D(photoTexture,texUV).rgb:c;
        vec3 surface=surfaceStyle<.5?base:surfaceStyle<1.5?base*(.8+.2*light):vec3(.7)*light;
        gl_FragColor=vec4(pointMode>.5?n:surface,1.);}
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
  load(mesh, points=false, textureUrl=null) {
    this.pointMode=points;
    this.hasColors=!!(mesh.colors||mesh.texture);
    this.textureMode=false;const version=this.textureVersion=(this.textureVersion||0)+1;
    const gl=this.gl, positions=new Float32Array(mesh.positions), normals=new Float32Array(points?mesh.colors:mesh.normals);
    if (!positions.length || positions.length%(points?3:9) || positions.length!==normals.length) throw new Error('Invalid preview mesh');
    // COLMAP camera coordinates have Y downward; rotate 180 degrees about X
    // for the browser's Y-up convention, preserving triangle winding.
    if(mesh.coordinate_system==='colmap')for(let i=0;i<positions.length;i++)if(i%3!==0){positions[i]*=-1;if(!points)normals[i]*=-1;}
    let lo=[Infinity,Infinity,Infinity], hi=[-Infinity,-Infinity,-Infinity];
    for(let i=0;i<positions.length;i++) {let k=i%3;lo[k]=Math.min(lo[k],positions[i]);hi[k]=Math.max(hi[k],positions[i]);}
    const scale=Math.max(...hi.map((x,i)=>x-lo[i]))/2||1;
    for(let i=0;i<positions.length;i++) positions[i]=(positions[i]-(lo[i%3]+hi[i%3])/2)/scale;
    for(const b of this.buffers) gl.deleteBuffer(b);
    this.buffers=[]; gl.useProgram(this.program);
    if(this.texture)gl.deleteTexture(this.texture);
    this.texture=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,this.texture);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,new Uint8Array([255,255,255,255]));
    const uv=mesh.uv?new Float32Array(mesh.uv):new Float32Array(positions.length/3*2);
    if(uv.length!==positions.length/3*2)throw new Error('Invalid mesh texture coordinates');
    const uvBuffer=gl.createBuffer();this.buffers.push(uvBuffer);gl.bindBuffer(gl.ARRAY_BUFFER,uvBuffer);gl.bufferData(gl.ARRAY_BUFFER,uv,gl.STATIC_DRAW);
    const uvLocation=gl.getAttribLocation(this.program,'uv');gl.enableVertexAttribArray(uvLocation);gl.vertexAttribPointer(uvLocation,2,gl.FLOAT,false,0,0);
    const colors=mesh.colors&&!points?new Float32Array(mesh.colors):new Float32Array(positions.length);
    if(!mesh.colors||points)for(let i=0;i<colors.length;i++)colors[i]=[.12,.57,.56][i%3];
    for(const [name,data] of [['position',positions],['normal',normals],['color',colors]]) {
      const b=gl.createBuffer();this.buffers.push(b);gl.bindBuffer(gl.ARRAY_BUFFER,b);gl.bufferData(gl.ARRAY_BUFFER,data,gl.STATIC_DRAW);
      const location=gl.getAttribLocation(this.program,name);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,3,gl.FLOAT,false,0,0);
    }
    this.count=positions.length/3; this.reset();
    if(mesh.texture&&textureUrl){
      const image=new Image();image.onload=()=>{
        if(this.textureVersion!==version)return;
        gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL,true);
        gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,image);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
        this.textureMode=true;this.draw();
      };image.src=textureUrl;
    }
  }
  draw() {
    const gl=this.gl;if(!gl||!this.program)return;
    const ratio=Math.min(devicePixelRatio||1,2);
    this.canvas.width=Math.round(this.canvas.clientWidth*ratio);this.canvas.height=Math.round(this.canvas.clientHeight*ratio);
    gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(.914,.933,.91,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);gl.useProgram(this.program);
    if(this.texture){gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.texture);gl.uniform1i(gl.getUniformLocation(this.program,'photoTexture'),0);}
    for(const [name,value] of Object.entries({yaw:this.yaw,pitch:this.pitch,zoom:this.zoom,aspect:this.canvas.width/Math.max(1,this.canvas.height),pointMode:this.pointMode?1:0,textureMode:this.textureMode?1:0,surfaceStyle:this.surfaceStyle??(this.hasColors?0:1)})) gl.uniform1f(gl.getUniformLocation(this.program,name),value);
    if(this.count)gl.drawArrays(this.pointMode?gl.POINTS:gl.TRIANGLES,0,this.count);
  }
}
window.MeshViewer=MeshViewer;
