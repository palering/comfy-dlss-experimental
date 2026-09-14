import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { setLocale } from '../web/i18n.js';
import { pipelineLabels, pipelineRows, PIPELINE_CARD_NODES } from '../web/pipeline_report.js';
import { previewRequest } from '../web/preview_state.js';

test('pipeline reports distinguish configuration from GPU and deferred guide execution', () => {
  setLocale('en');
  assert.match(pipelineLabels().note, /not GPU acceptance/);
  const rows = pipelineRows({source:{width:64,height:64},stages:[],motion:{provider:'nvidia',usage:'deferred'}});
  assert.ok(rows.some(r => r[1] === 'nvidia'));
  assert.ok(rows.some(r => r[1] === 'Awaiting feature requirements'));
  assert.ok(rows.some(r => r[1] === 'Unknown'));
  setLocale('zh');
  assert.match(pipelineLabels().note, /不是 GPU 验收/);
  assert.ok(pipelineRows({attachments:[{role:'depth'}]}).some(r => r[1] === '当前后端不消费'));
});

test('pipeline preview button includes stages but excludes downstream full export', () => {
  const graph = {1:{inputs:{}},2:{inputs:{sequence:['1',0]}},3:{inputs:{pipeline:['2',0]}},
    4:{class_type:'DLSSExperimentalPipelinePreview',inputs:{pipeline:['3',0],duration:3}},
    5:{class_type:'DLSSExperimentalPipelineRender',inputs:{pipeline:['3',0]}},
    6:{class_type:'SaveVideo',inputs:{video:['5',0]}}};
  const output = previewRequest(graph,4,'frame',.5);
  assert.deepEqual(Object.keys(output), ['1','2','3','4']);
  assert.equal(output[4].inputs.preview_mode,'frame');
  assert.equal(graph[4].inputs.preview_mode,undefined);
});

test('pipeline cards keep DOM text safe, resizable and out of saved workflows', () => {
  assert.ok(PIPELINE_CARD_NODES.has('DLSSExperimentalNRStage'));
  const source = readFileSync(new URL('../web/pipeline_card.js',import.meta.url),'utf8');
  assert.ok(!source.includes('innerHTML'));
  assert.match(source,/serialize:false/);
  assert.match(source,/getMaxHeight:\(\) => 2000/);
  assert.match(source,/onConnectionsChange/);
  assert.match(source,/copyDiagnosticText/);
  const preview = readFileSync(new URL('../web/dlss_preview.js',import.meta.url),'utf8');
  assert.match(preview,/DLSSExperimentalPipelinePreview/);
});

test('external motion use and SR blocked plans stay visible in the node card', () => {
  setLocale('en');
  assert.ok(pipelineRows({attachments:[{role:'motion',usage:'required'}]})
    .some(row => row[1] === 'Read external numeric data'));
  assert.ok(PIPELINE_CARD_NODES.has('DLSSExperimentalSRPlan'));
  const rows = pipelineRows({kind:'sr_input_plan', feature:'sr', input:{width:640,height:360},
    output:{width:1280,height:720}, missing_inputs:['depth']});
  assert.ok(rows.some(row => row[1] === '1280 × 720'));
  assert.ok(rows.some(row => row[1] === 'depth'));
  assert.ok(rows.some(row => row[1] === 'SR requires SDK Worker and GPU validation'));
  const source = readFileSync(new URL('../web/pipeline_card.js',import.meta.url),'utf8');
  assert.match(source, /incoming\?\.kind === "sr_input_plan"/);
});

test('Streamline stage uses its own runtime readiness label', () => {
  setLocale('en');
  assert.ok(PIPELINE_CARD_NODES.has('DLSSExperimentalStreamlineStage'));
  const rows = pipelineRows({feature:'dlaa', stages:[{backend:'owned_cxr1_sl'}], output:{width:640,height:360},
    missing_inputs:['camera']});
  assert.ok(rows.some(row => row[1] === 'camera'));
  assert.ok(rows.some(row => row[1] === 'Streamline requires owned_sl; this plan has not run the GPU'));
});
