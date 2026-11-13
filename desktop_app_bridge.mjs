import net from 'node:net';
import { randomUUID } from 'node:crypto';

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const request = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const socket = net.createConnection(request.pipe);
let buffer = Buffer.alloc(0);
let tool;
let toolRequested = false;
const timeout = setTimeout(() => fail(new Error('Codex 桌面项目接口超时')), 15000);

function fail(error) {
    clearTimeout(timeout);
    socket.destroy();
    process.stderr.write(error.message + '\n');
    process.exitCode = toolRequested ? 1 : 2;
}

function send(message) {
    const payload = Buffer.from(JSON.stringify(message), 'utf8');
    const header = Buffer.alloc(4);
    header.writeUInt32LE(payload.length);
    socket.write(Buffer.concat([header, payload]));
}

socket.on('error', fail);
socket.on('connect', () => send({ id: 1, jsonrpc: '2.0', method: 'tools/list' }));
socket.on('data', chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    while (buffer.length >= 4) {
        const size = buffer.readUInt32LE(0);
        if (size > 8 * 1024 * 1024) return fail(new Error('桌面接口响应超过限制'));
        if (buffer.length < size + 4) return;
        const message = JSON.parse(buffer.subarray(4, size + 4).toString('utf8'));
        buffer = buffer.subarray(size + 4);
        if (message.error) return fail(new Error(message.error.message));
        if (message.id === 1) {
            tool = message.result.tools.find(item => item.name === request.tool);
            if (!tool) return fail(new Error('桌面端未提供所需项目接口'));
            toolRequested = true;
            send({ id: 2, jsonrpc: '2.0', method: 'tools/call', params: {
                namespace: tool.namespace, tool: tool.name, arguments: request.arguments || {},
                threadId: request.threadId, callerSource: 'codex', hostId: 'local',
                callId: 'novel-hub-' + randomUUID(), turnId: 'novel-hub-project-bind-' + randomUUID()
            }});
        } else if (message.id === 2) {
            clearTimeout(timeout);
            socket.end();
            const result = message.result;
            const text = (result.contentItems || []).filter(item => item.type === 'inputText').map(item => item.text).join('\n');
            if (!result.success) return fail(new Error(text || '桌面项目操作失败'));
            process.stdout.write(JSON.stringify({ text }));
        }
    }
});
