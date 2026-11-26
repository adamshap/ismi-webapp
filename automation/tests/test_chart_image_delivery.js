const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(path.join(__dirname, '../../ism_webapp.html'), 'utf8');
const source = html.slice(html.indexOf('        function canShareChartImage'), html.indexOf('        let preparedChartImage'))
    + html.slice(html.indexOf('        async function deliverChartImage'), html.indexOf('        let sharingChart'));
async function delivery({mobile = true, shareable = true, error = null, action = 'share'} = {}) {
    const result = {shares: [], previews: [], downloads: [], revoked: []};
    const status = {textContent: ''};
    const context = vm.createContext({
        File, Blob,
        window: {matchMedia: () => ({matches: mobile})},
        navigator: {
            canShare: () => shareable,
            share: async data => {result.shares.push(data); if (error) throw Object.assign(new Error(error), {name: error});}
        },
        openChartImagePreview: (file, title) => result.previews.push({file, title}),
        document: {
            getElementById: () => status,
            body: {appendChild() {}},
            createElement: () => {const link = {click() {result.downloads.push({filename: link.download});}, remove() {}}; return link;}
        },
        URL: {createObjectURL: () => 'blob:test-image', revokeObjectURL: url => result.revoked.push(url)},
        setTimeout: callback => callback()
    });
    vm.runInContext(source, context);
    context.blob = new Blob(['original PNG bytes'], {type: 'image/png'});
    await vm.runInContext(`deliverChartImage(blob, 'ismi_chart.png', 'ISMI', '${action}')`, context);
    return result;
}
(async () => {
    let r = await delivery();
    assert.equal(r.shares.length, 1);
    assert.equal(r.shares[0].files[0].type, 'image/png');
    assert.equal(await r.shares[0].files[0].text(), 'original PNG bytes');
    assert.equal(r.downloads.length, 0);
    assert.equal(r.previews.length, 0);
    r = await delivery({error: 'AbortError'});
    assert.equal(r.downloads.length, 0, 'Cancelling never triggers a download');
    assert.equal(r.previews.length, 0, 'Cancelling never forces another dialog');
    for (const options of [{shareable: false}, {error: 'NotAllowedError'}]) {
        r = await delivery(options);
        assert.equal(r.previews.length, 1, 'Unsupported sharing or expired activation opens the saveable image');
        assert.equal(await r.previews[0].file.text(), 'original PNG bytes');
        assert.equal(r.downloads.length, 0);
    }
    for (const options of [{action: 'download'}, {mobile: false, shareable: false}]) {
        r = await delivery(options);
        assert.equal(r.downloads.length, 1, 'Explicit downloads and desktop fallback remain available');
        assert.equal(r.downloads[0].filename, 'ismi_chart.png');
        assert.equal(r.previews.length, 0);
    }
    // A retry from the preview calls share synchronously, retaining the fresh tap.
    const retrySource = html.slice(html.indexOf('        async function sharePreparedChart'), html.indexOf("        document.getElementById('chart-image-dialog').addEventListener"));
    let immediateShares = 0, closed = false;
    const button = {disabled: false}, status = {textContent: ''};
    const retry = vm.createContext({
        preparedChartImage: {file: new File(['PNG'], 'chart.png', {type: 'image/png'}), title: 'ISMI'},
        navigator: {share: () => {immediateShares++; return Promise.resolve();}},
        document: {getElementById: id => id === 'image-native-share' ? button : id === 'image-save-status' ? status : {close() {closed = true;}}}
    });
    vm.runInContext(retrySource, retry);
    const shared = vm.runInContext('sharePreparedChart()', retry);
    assert.equal(immediateShares, 1, 'The retry must call share before any await');
    await shared;
    assert.equal(closed, true);
    assert.equal(button.disabled, false);
    console.log('PNG delivery: native sharing, cancel, mobile preview fallback, and explicit/desktop downloads passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
