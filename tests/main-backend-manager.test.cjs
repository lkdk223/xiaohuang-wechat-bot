const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');

const { createBackendManager } = require('../src/main/backend-manager');

function createBackendManagerHarness(options = {}) {
    const timers = [];
    const execFileCalls = [];
    const backendIssues = [];
    let nextTimerId = 1;
    const proc = new EventEmitter();
    proc.pid = options.pid || 1234;
    proc.killCalls = [];
    proc.kill = (signal) => {
        proc.killCalls.push(signal);
        if (options.killThrows) {
            throw new Error('kill failed');
        }
        return true;
    };

    const GLOBAL_STATE = {
        flaskPort: 5000,
        apiToken: 'unit-token',
        pythonProcess: proc,
    };
    const manager = createBackendManager({
        http: options.http || {},
        spawn: () => proc,
        execFile: (file, args, callback) => {
            execFileCalls.push({ file, args });
            callback?.(options.execFileError || null);
        },
        GLOBAL_STATE,
        getBackendCommand: () => ({ cmd: 'python', args: [], options: {} }),
        getMainWindowVisible: () => true,
        updateSplashStatus: () => {},
        runtimeIdleController: {
            setWindowVisible: () => {},
            setServiceRunning: () => {},
            setServiceStopped: () => {},
        },
        platform: options.platform || 'win32',
        onBackendProcessIssue: (issue) => backendIssues.push(issue),
        setTimer: (fn, delay) => {
            const timer = {
                id: nextTimerId++,
                canceled: false,
                delay,
                fn() {
                    if (!timer.canceled) {
                        fn();
                    }
                },
            };
            timers.push(timer);
            return timer.id;
        },
        clearTimer: (id) => {
            const timer = timers.find((item) => item.id === id);
            if (timer) {
                timer.canceled = true;
            }
        },
    });

    return { manager, GLOBAL_STATE, proc, timers, execFileCalls, backendIssues };
}

test('BackendManager.requestJson rejects interrupted response bodies', async () => {
    const response = new EventEmitter();
    const request = new EventEmitter();
    request.end = () => {};
    const { manager } = createBackendManagerHarness({ http: {
        request: (_options, callback) => {
            callback(response);
            return request;
        },
    } });
    const pending = manager.requestJson('GET', '/api/status');
    const rejected = assert.rejects(pending, (error) =>
        error.code === 'ECONNRESET' && error.endpoint === '/api/status');
    const error = Object.assign(new Error('response interrupted'), { code: 'ECONNRESET' });
    assert.doesNotThrow(() => response.emit('error', error));
    await rejected;
});

test('BackendManager.requestJson handles a real truncated HTTP response', async () => {
    const http = require('node:http');
    const server = http.createServer((_req, res) => {
        res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '100' });
        res.write('{"partial":');
        setImmediate(() => res.destroy());
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { manager, GLOBAL_STATE } = createBackendManagerHarness({ http });
    GLOBAL_STATE.flaskPort = server.address().port;
    try {
        await assert.rejects(manager.requestJson('GET', '/api/status', null, 1000),
            (error) => error.code === 'ECONNRESET' && error.endpoint === '/api/status');
    } finally {
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
    }
});

test('BackendManager.stop uses taskkill tree cleanup on Windows after graceful timeout', async () => {
    const { manager, GLOBAL_STATE, proc, timers, execFileCalls } = createBackendManagerHarness();

    const stopPromise = manager.stop('install-update');

    assert.deepEqual(proc.killCalls, ['SIGTERM']);
    assert.equal(proc.__backendStopReason, 'install-update');
    assert.equal(GLOBAL_STATE.pythonProcess, null);
    assert.deepEqual(timers.map((timer) => timer.delay), [3000, 3500]);

    timers.find((timer) => timer.delay === 3000).fn();
    assert.deepEqual(execFileCalls, [
        {
            file: 'taskkill.exe',
            args: ['/PID', '1234', '/T', '/F'],
        },
    ]);

    timers.find((timer) => timer.delay === 3500).fn();
    await stopPromise;
});

test('BackendManager.stop resolves immediately when graceful stop cannot be signaled', async () => {
    const { manager, proc, execFileCalls } = createBackendManagerHarness({ killThrows: true });

    await manager.stop('quit');

    assert.deepEqual(proc.killCalls, ['SIGTERM']);
    assert.deepEqual(execFileCalls, []);
});

test('BackendManager.stop cancels force kill fallback after graceful exit', async () => {
    const { manager, proc, timers, execFileCalls } = createBackendManagerHarness();

    const stopPromise = manager.stop('quit');
    proc.emit('exit', 0);
    await stopPromise;

    timers.find((timer) => timer.delay === 3000).fn();

    assert.deepEqual(execFileCalls, []);
});

test('BackendManager reports unexpected backend process exit', () => {
    const { manager, proc, backendIssues } = createBackendManagerHarness();

    manager._setupProcessListeners(proc);
    proc.emit('exit', 2);

    assert.deepEqual(backendIssues, [
        {
            type: 'unexpected_exit',
            reason: 'process_exit',
            code: 2,
            signal: null,
        },
    ]);
});

test('BackendManager does not report planned backend process stop as an issue', async () => {
    const { manager, proc, backendIssues } = createBackendManagerHarness();

    manager._setupProcessListeners(proc);
    const stopPromise = manager.stop('quit');
    proc.emit('exit', 0);
    await stopPromise;

    assert.deepEqual(backendIssues, []);
});

test('BackendManager reports backend spawn errors without leaking error text', () => {
    const { manager, proc, backendIssues } = createBackendManagerHarness();

    manager._setupProcessListeners(proc);
    proc.emit('error', new Error('secret backend launch path'));

    assert.deepEqual(backendIssues, [
        {
            type: 'spawn_error',
            reason: 'spawn_error',
        },
    ]);
});

test('BackendManager reports only one issue when spawn error is followed by exit', () => {
    const { manager, proc, backendIssues } = createBackendManagerHarness();

    manager._setupProcessListeners(proc);
    proc.emit('error', new Error('launch failed'));
    proc.emit('exit', 1);

    assert.deepEqual(backendIssues, [
        {
            type: 'spawn_error',
            reason: 'spawn_error',
        },
    ]);
});
