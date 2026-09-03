import test from 'node:test';
import assert from 'node:assert/strict';
import { setLocale, getLocale, normalizeLocale, t, onLocaleChange, bindText, disposeTranslations } from '../web/i18n.js';
import { english, chinese } from '../web/i18n_messages.js';
import { reportSections, reportIssue } from '../web/input_report.js';
import { historyLines, residentLines } from '../web/runtime_monitor_state.js';
import { previewParameterHelp, previewOutputNotice } from '../web/preview_help.js';
import { applyOptionLabels } from '../web/i18n_options.js';
import { readFileSync } from 'node:fs';

test('combo display labels preserve raw values, callbacks and option arrays', () => {
  const en = JSON.parse(readFileSync(new URL('../locales/en/nodeDefs.json', import.meta.url)));
  const zh = JSON.parse(readFileSync(new URL('../locales/zh/nodeDefs.json', import.meta.url)));
  const values = ['默认风格', '自然 · Natural', '电影感 · Cinematic'];
  const callback = () => assert.fail('localization must not execute callbacks');
  const widget = {name:'nr_style',type:'combo',value:values[2],options:{values},callback};
  const node = {comfyClass:'DLSSExperimentalNRProfile',widgets:[widget]};
  const options = widget.options;
  applyOptionLabels(node,en);
  assert.equal(widget.options, options, 'Nodes 2.0 shares this reference');
  const first = widget.options.getOptionLabel;
  assert.equal(first(widget.value), 'Cinematic');
  assert.equal(first('unknown style'), 'unknown style');
  applyOptionLabels(node,zh);
  assert.equal(widget.options, options);
  assert.notEqual(widget.options.getOptionLabel, first);
  assert.equal(widget.options.getOptionLabel(widget.value), '电影感');
  assert.equal(widget.value,values[2]);
  assert.equal(widget.options.values,values);
  assert.equal(widget.callback,callback);
});

test('Comfy language normalization has a deterministic English fallback', () => {
  for (const value of ['zh','zh-CN','zh_TW','zh-Hans']) assert.equal(normalizeLocale(value),'zh');
  for (const value of ['en','en-US','ja',undefined,'zhwhatever']) assert.equal(normalizeLocale(value),'en');
  setLocale('en'); assert.equal(getLocale(),'en'); assert.equal(t('渲染当前帧'),'Render current frame');
  setLocale('zh'); assert.equal(t('渲染当前帧'),'渲染当前帧');
  assert.equal(t('Adapted original'),'适配后的原始输入');
});

test('template substitution preserves arbitrary diagnostic values without evaluation', () => {
  setLocale('en');
  const input = '$& <script>bad()</script> {1}';
  assert.equal(t`源文件 ${input}`, `Source: ${input}`);
  assert.equal(t('unrecognized low-level error: 0xBAD00002'),'unrecognized low-level error: 0xBAD00002');
  for (const [source, translated] of Object.entries(english)) {
    assert.deepEqual([...source.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(), [...translated.matchAll(/\{\d+\}/g)].map(m=>m[0]).sort(), source);
    assert.doesNotMatch(translated, /[\u3400-\u9fff]/u, source);
  }
  assert.ok(Object.keys(chinese).length);
});

test('reports, history and help switch without changing their source data', () => {
  const report={filename:'用户素材.mp4',state:'ready',guide_mode:'nvidia',source_video:{color_transfer:'bad'},
    policy:{normalize_to_srgb:true},color_normalization:{operation:'bt709_to_srgb'},
    plan:['仅解码请求区间及其前置帧。']};
  const original=JSON.stringify(report);
  setLocale('en');
  assert.equal(reportSections(report)[0][1][0][1],'用户素材.mp4');
  assert.equal(reportSections(report)[0][0],'File and image');
  assert.match(reportIssue({kind:'needs_confirmation',field:'color_transfer'},report), /Missing color_transfer/);
  assert.match(reportIssue({kind:'unsupported',field:'color_transfer'},report), /color_transfer=bad/);
  assert.equal(reportIssue({message:'external 0x123'},report),'external 0x123');
  assert.doesNotMatch(historyLines({}).join('\n'),/[\u3400-\u9fff]/u);
  const resident={state:'idle',worker_id:'test',settings:{width:64,height:64},idle_remaining_seconds:30,leases:1,stream_frames:2};
  assert.doesNotMatch(residentLines(resident).join('\n'),/[\u3400-\u9fff]/u);
  assert.doesNotMatch(JSON.stringify(previewParameterHelp())+previewOutputNotice(),/[\u3400-\u9fff]/u);
  setLocale('zh');
  assert.equal(reportSections(report)[0][0],'文件与画面');
  assert.match(previewOutputNotice(),/保存/);
  assert.equal(JSON.stringify(report),original);
});

test('explicit UI bindings update and unsubscribe when a card is removed', () => {
  setLocale('en');
  const child={}, root={contains: value=>value===child};
  bindText(child,()=>t('渲染当前帧'));
  let calls=0; const unsubscribe=onLocaleChange(()=>calls++);
  setLocale('zh'); assert.equal(child.textContent,'渲染当前帧'); assert.equal(calls,1);
  setLocale('zh'); assert.equal(calls,1);
  unsubscribe(); disposeTranslations(root);
  setLocale('en'); assert.equal(child.textContent,'渲染当前帧'); assert.equal(calls,1);
});

test('media executable report is bilingual and preserves paths and errors', () => {
  const report = {media_tools:{pyav_version:'18',ffmpeg:{available:true,path:'工具/bin/ffmpeg',source:'explicit',version:'ffmpeg version test'},
    ffprobe:{available:false,source:'ffmpeg_directory',error:'external error'}}};
  setLocale('en');
  const section = reportSections(report).at(-1);
  assert.equal(section[0], 'Media tools (backend host)');
  assert.ok(section[1].some(([label,value])=>label==='Resolved path' && value==='工具/bin/ffmpeg'));
  assert.ok(section[1].some(([,value])=>value==='external error'));
  setLocale('zh');
  assert.equal(reportSections(report).at(-1)[0], '媒体工具（宿主系统）');
});
