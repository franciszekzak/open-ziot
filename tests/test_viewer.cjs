// Run the viewer script in an isolated DOM stub, without a browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../server/viewer.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
const timers = new Map();
let timerId = 0, fetches = 0, onLoad;
const elements = new Map();
const sandbox = {
  AbortController, Response, URL, Date, console,
  document: {getElementById(id) {
    if (!elements.has(id)) elements.set(id, {textContent: '', value: '10'});
    return elements.get(id);
  }},
  window: {addEventListener(event, callback) {assert.equal(event, 'load'); onLoad = callback;}},
  setTimeout(callback, delay) {const id = ++timerId; timers.set(id, {callback, delay}); return id;},
  clearTimeout(id) {timers.delete(id);},
  fetch: async () => {fetches++; return new Response('hello');},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
assert.equal(fetches, 0, 'Network polling must wait until page load');
assert.equal(typeof onLoad, 'function');
(async () => {
  const response = await vm.runInContext("request('snapshot.jpg')", sandbox);
  assert.equal(await response.text(), 'hello');
  assert.equal(timers.size, 0, 'Successful requests clear their timeout');
  // A response can send headers and then stall indefinitely in its body.
  sandbox.fetch = async (_, options) => ({
    arrayBuffer: () => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted')));
    }),
  });
  const pending = vm.runInContext("request('snapshot.jpg')", sandbox);
  await new Promise(resolve => setImmediate(resolve));
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 8000);
  timer.callback();
  await assert.rejects(pending, /aborted/);
  assert.equal(timers.size, 0);
  console.log('Viewer startup and stalled-response recovery passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
