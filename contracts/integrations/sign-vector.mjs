// 测试向量中的密钥为公开样例；本脚本只在业务后端或离线测试运行。
import { createHmac } from 'node:crypto'
let input = ''
for await (const chunk of process.stdin) input += chunk
const vector = JSON.parse(input)
const payload = Buffer.from(JSON.stringify(vector.claims), 'utf8').toString('base64url')
const message = `business-delegation-v2\n${vector.kid}\n${payload}`
const signature = createHmac('sha256', Buffer.from(vector.secret_hex, 'hex')).update(message).digest('base64url')
process.stdout.write(`v2.${vector.kid}.${payload}.${signature}\n`)
