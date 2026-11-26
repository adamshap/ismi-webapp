const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../../ism_webapp.html'), 'utf8');
const match = html.match(/async function updateDataStatus\(\) \{[\s\S]*?(?=\n        function updateDateDisplays)/);
assert.ok(match, 'Dashboard release-date function exists');
async function render(metadata, ok = true) {
    const elements = Object.fromEntries(['dataThrough', 'dataUpdate', 'lastUpdated'].map(id => [id, {hidden: true, textContent: ''}]));
    await vm.runInNewContext(`(async () => { ${match[0]}; await updateDataStatus(); })()`, {
        document: {getElementById: id => elements[id]},
        allData: [{date: '2026-10'}],
        fetch: async () => ({ok, json: async () => metadata}),
        Date, console
    });
    return elements;
}
(async () => {
    let result = await render({data_through: '2026-10', bea_release_date: '2026-11-25'});
    assert.equal(result.lastUpdated.textContent, 'Last updated November 25, 2026');
    assert.equal(result.lastUpdated.dateTime, '2026-11-25');
    assert.equal(result.lastUpdated.hidden, false);
    assert.equal(result.dataThrough.textContent, 'Data through October 2026');
    result = await render({data_through: '2026-11', bea_release_date: '2026-12-23'});
    assert.equal(result.lastUpdated.hidden, true, 'Do not show a new release date with an old CSV');
    result = await render({}, false);
    assert.equal(result.lastUpdated.hidden, true);
    assert.equal(result.dataThrough.textContent, 'Data through October 2026');
    console.log('Dashboard date display and mismatched-metadata checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
