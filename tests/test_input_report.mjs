import test from 'node:test';
import assert from 'node:assert/strict';
import { known, reportSections } from '../web/input_report.js';
import { setLocale } from '../web/i18n.js';
setLocale('zh');

test('normalization distinguishes working transfer from source interpretation', () => {
  const rows = reportSections({policy:{normalize_to_srgb:true}, effective_color:{color_transfer:'bt709'},
    color_normalization:{operation:'bt709_to_srgb',working_transfer:'iec61966-2-1',output_transfer:'bt709'}})[2][1];
  assert.ok(rows.some(([label,value]) => label === '转换状态' && value.includes('实际转换像素')));
  assert.ok(rows.some(([label,value]) => label === '工作空间' && value.includes('sRGB')));
  assert.ok(rows.some(([label,value]) => label === '导出传递曲线' && value.includes('BT.709')));
  const blocked = reportSections({policy:{normalize_to_srgb:true},color_normalization:{operation:'blocked'}})[2][1];
  assert.ok(blocked.some(([label,value]) => label === '工作空间' && value === '待输入检查'));
});

test('NVIDIA and unknown providers are not mislabeled DIS', () => {
  const mode = value => reportSections({guide_mode:value}).at(-1)[1][0][1];
  assert.match(mode('nvidia'), /NVIDIA/);
  assert.equal(mode(undefined), '未知');
  assert.match(mode('dis'), /DIS/);
});

test('unknown metadata is not presented as a fabricated value', () => {
  assert.equal(known(undefined), '未知');
  assert.equal(known(0), '0');
  const sections = reportSections({});
  assert.ok(sections.flatMap(x => x[1]).some(row => row[1].includes('未逐帧计数')));
});
test('assumptions are labelled separately from source color and output remains text', () => {
  const sections = reportSections({filename:'<script>alert(1)</script>',
    effective_color:{color_transfer:'bt709'}, assumptions:[{field:'color_transfer'}], policy:{mode:'fill_missing'}});
  assert.equal(sections[0][1][0][1], '<script>alert(1)</script>');
  assert.ok(sections[2][1].some(row => row[1] === '未知 → bt709（用户假设）'));
});
