import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import test from "node:test";

function fixture(type, native=true) {
  let extension, disposed=false;
  const context=vm.createContext({app:{registerExtension(e){extension=e;}},t:x=>x,
    onLocaleChange:()=>()=>{disposed=true;},document:{createElement:()=>({style:{}})}});
  vm.runInContext(readFileSync(new URL("../web/conditional_controls.js",import.meta.url),"utf8")
    .replace(/^import .*;$/gm,"").replace("export function","function"),context);
  class Node {
    constructor() {
      this.size=[420,400];this.inputs=[];
      this.widgets=Object.entries({mode:"quality",output_width:960,output_height:540,output_size_mode:"manual",start_time:2,duration:3,process_to_end:false})
        .map(([name,value])=>({name,value,type:typeof value,options:{},callback(){this.called=true;}}));
    }
    setSize(size){this.size=size;} computeSize(){return [340,350];} setDirtyCanvas(){}
    addDOMWidget(name,type,element,options){const w={name,type,element,options};this.widgets.push(w);return w;}
  }
  if(native)Node.prototype.isWidgetVisible=w=>!w.hidden;
  extension.beforeRegisterNodeDef(Node,{name:type});const node=new Node();node.onNodeCreated();
  const get=name=>node.widgets.find(w=>w.name===name);
  const change=(name,value)=>{const w=get(name);w.value=value;w.callback(value);};
  return {node,get,change,disposed:()=>disposed};
}

for (const native of [true,false]) {
  test(`multiplier uses native parameter controls and preserves manual values, native=${native}`,()=>{
    const {node,get,change}=fixture("DLSSExperimentalStreamlineStage",native);
    change("output_size_mode","2x");assert.equal(get("output_width").hidden,true);
    node.onConfigure();assert.equal(get("output_width").value,960);
    change("mode","dlaa");assert.equal(get("output_size_mode").hidden,true);
    change("mode","quality");assert.equal(get("output_size_mode").hidden,false);
    change("output_size_mode","manual");assert.equal(get("output_width").hidden,false);
    assert.equal(get("output_width").value,960);
    get("output_size_mode").value="";node.onConfigure();assert.equal(get("output_size_mode").value,"manual");
  });
  test(`DLAA visibility and round-trip values, native=${native}`,()=>{
    const {node,get,change}=fixture("DLSSExperimentalStreamlineStage",native);
    const options=get("output_width").options;
    change("mode","dlaa");assert.equal(get("output_width").hidden,true);assert.equal(get("output_height").options.hidden,true);
    assert.equal(get("output_width").value,960);assert.equal(get("output_width").type,native?"number":"hidden");
    node.onConfigure();assert.equal(get("output_width").hidden,true);
    change("mode","quality");assert.equal(get("output_width").hidden,false);assert.equal(get("output_width").type,"number");
    assert.equal(get("output_width").options,options);assert.equal(get("output_width").computeSize,undefined);
    assert.equal(get("mode").called,true);assert.equal(get("dlss_conditional_help").options.serialize,false);
  });
  for(const type of ["DLSSExperimentalPipelineRender","DLSSExperimentalProcessVideo","DLSSExperimentalPipelinePreview","DLSSExperimentalPreviewSession"])
    test(`${type} to-end preserves start and duration, native=${native}`,()=>{
      const {node,get,change,disposed}=fixture(type,native);
      change("process_to_end",true);assert.equal(get("duration").hidden,true);assert.equal(get("start_time").hidden,undefined);
      assert.equal(get("duration").value,3);node.onConfigure();assert.equal(get("duration").hidden,true);
      change("process_to_end",false);assert.equal(get("duration").hidden,false);assert.equal(get("duration").value,3);
      node.onRemoved();assert.equal(disposed(),true);
    });
}

test("connected mode does not hide controls based on a stale local value",()=>{
  const {node,get,change}=fixture("DLSSExperimentalStreamlineStage");change("mode","dlaa");
  node.inputs.push({name:"mode",link:17});node.onConnectionsChange();
  assert.equal(get("output_width").hidden,false);assert.equal(node.inputs[0].link,17);
  assert.match(get("dlss_conditional_help").element.textContent,/连线/);
});
