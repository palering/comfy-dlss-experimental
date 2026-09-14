import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeDiagnosticReport } from '../web/diagnostic_report.js';
import { reportSections } from '../web/input_report.js';
import { pipelineRows } from '../web/pipeline_report.js';

test('new JSON transport and old workflow objects retain nested report data', () => {
  const report = {state:'ready', format:{format_name:'mov,mp4',duration:'0.267'},
    filename:'输入 <script>.mp4', issues:[], source_video:{width:640,height:360}};
  assert.equal(decodeDiagnosticReport(report), report);
  const decoded = decodeDiagnosticReport(JSON.stringify(report));
  assert.deepEqual(decoded, report);
  assert.deepEqual(reportSections(decoded), reportSections(report));
});

test('pipeline and preview payloads preserve their original contracts', () => {
  for (const report of [{kind:'media_pipeline_report', stages:[], source:{width:640,height:360}},
    {kind:'sr_input_plan', feature:'dlaa', output:{width:640,height:360}},
    {session_id:'abc', videos:{a:'a.mp4',b:'b.mp4'}, state:'complete'}]) {
    assert.deepEqual(decodeDiagnosticReport(JSON.stringify(report)), report);
    if (report.kind) assert.deepEqual(pipelineRows(decodeDiagnosticReport(JSON.stringify(report))), pipelineRows(report));
  }
});

test('malformed and non-object payloads are ignored without throwing', () => {
  for (const value of [undefined,null,0,true,[],new Date(),'{bad','null','[]','42','"text"','']) {
    assert.equal(decodeDiagnosticReport(value), null);
  }
});
