"use strict";
// import { RDSDataClient, ExecuteStatementCommand } from '@aws-sdk/client-rds-data';
// import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager';
Object.defineProperty(exports, "__esModule", { value: true });
exports.handler = void 0;
// const rdsDataClient = new RDSDataClient({});
// const secretsManagerClient = new SecretsManagerClient({});
// exports.handler = async (event: any) => {
//   const tenantId = event.tenantId; // 테넌트 ID
//   const dbProxyArn = process.env.DB_PROXY_ARN;
//   const secretArn = process.env.SECRET_ARN;
//   try {
//     // RDS 비밀에서 자격 증명을 가져옴
//     const secret = await secretsManagerClient.send(
//       new GetSecretValueCommand({ SecretId: secretArn })
//     );
//     const credentials = JSON.parse(secret.SecretString!);
//     // 테넌트의 스키마 생성 SQL
//     const sql = `
//       CREATE SCHEMA IF NOT EXISTS tenant_${tenantId};
//       CREATE TABLE IF NOT EXISTS tenant_${tenantId}.Orders (
//         orderId INT PRIMARY KEY AUTO_INCREMENT,
//         orderDate TIMESTAMP DEFAULT CURRENT_TIMESTAMP
//       );
//       CREATE TABLE IF NOT EXISTS tenant_${tenantId}.Products (
//         productId INT PRIMARY KEY AUTO_INCREMENT,
//         productName VARCHAR(255)
//       );
//     `;
//     // RDS Proxy를 통해 SQL 실행
//     const command = new ExecuteStatementCommand({
//       resourceArn: dbProxyArn,
//       secretArn: secretArn,
//       sql: sql,
//       database: 'main_db',
//     });
//     await rdsDataClient.send(command);
//     return {
//       statusCode: 200,
//       body: JSON.stringify({ message: `Schema created for tenant_${tenantId}` }),
//     };
//   } catch (error) {
//     console.error('Error creating schema:', error);
//     return {
//       statusCode: 500,
//       body: JSON.stringify({ message: 'Error creating schema', error }),
//     };
//   }
// };
const AWS = require("aws-sdk");
const mysql = require("mysql2/promise");
const client_secrets_manager_1 = require("@aws-sdk/client-secrets-manager");
const secretsmanager = new client_secrets_manager_1.SecretsManagerClient({ region: process.env.REGION });
const rds = new AWS.RDS();
const ENDPOINT = process.env.DB_ENDPOINT;
const PORT = 3306;
const USR = process.env.USER;
// const REGION = process.env.REGION as string;
const DBNAME = process.env.DB_NAME;
const PROXY_NAME = process.env.DB_PROXY_NAME;
const secretArn = process.env.DB_SECRET_ARN;
const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
// Lambda 핸들러
const handler = async (event) => {
    const tenantId = event.tenantId; // 테넌트 ID는 이벤트로부터 전달
    if (!tenantId) {
        throw new Error('Tenant ID is required');
    }
    let connection;
    try {
        // Secrets Manager에서 DB 비밀번호 가져오기
        const secretValue = await secretsmanager.send(new client_secrets_manager_1.GetSecretValueCommand({ SecretId: secretArn }));
        const secretData = JSON.parse(secretValue.SecretString || '{}');
        const dbPassword = secretData.password;
        // MySQL 연결 설정
        connection = await mysql.createConnection({
            host: ENDPOINT,
            user: USR,
            password: dbPassword,
            port: PORT,
            database: DBNAME,
        });
        // 테넌트에 대한 데이터베이스가 이미 존재하는지 확인
        const dbName = `tenant_${tenantId}_db`;
        const dbCheckQuery = `SHOW DATABASES LIKE '${dbName}'`;
        const [dbCheckResult] = await connection.query(dbCheckQuery);
        if (Array.isArray(dbCheckResult) && dbCheckQuery.length === 0) {
            console.log(`Database for tenant ${tenantId} does not exist. Creating now...`);
            // 데이터베이스 및 테이블 생성
            await createTenantDatabaseAndTables(connection, tenantId, dbPassword);
        }
        else {
            console.log(`Database for tenant ${tenantId} already exists. Skipping creation.`);
        }
        console.log('Success');
    }
    catch (error) {
        console.error(`Error: ${error}`);
        throw new Error(`Database connection or schema creation failed due to ${error}`);
    }
    finally {
        if (connection) {
            await connection.end();
        }
    }
};
exports.handler = handler;
// 테넌트에 대한 데이터베이스 및 테이블 생성
async function createTenantDatabaseAndTables(connection, tenantId, dbPassword) {
    const dbusername = `user_${tenantId}`;
    const dbname = `tenant_${tenantId}_db`;
    const userPassword = generatePassword(32);
    try {
        // 사용자 및 스키마 생성 쿼리 실행
        const queries = [
            `CREATE USER '${dbusername}' IDENTIFIED BY '${userPassword}';`,
            `CREATE DATABASE ${dbname};`,
            `GRANT CREATE VIEW, SHOW VIEW, SELECT, INSERT, UPDATE ON ${dbname}.* TO '${dbusername}';`,
            `USE ${dbname}`,
            `CREATE TABLE orders (
        order_id INT AUTO_INCREMENT PRIMARY KEY,
        product_id INT,
        quantity INT,
        total_price DECIMAL(10, 2)
      );`,
            `CREATE TABLE products (
        product_id INT AUTO_INCREMENT PRIMARY KEY,
        product_name VARCHAR(255),
        product_description TEXT,
        price DECIMAL(10, 2)
      );`,
        ];
        for (const query of queries) {
            await connection.query(query);
        }
        // Secrets Manager에 사용자 비밀 저장
        const secretName = `Amazon_rds_proxy_multitenant/${tenantId}_user_secret`;
        const secretDescription = `Proxy secret created for tenant ${tenantId}`;
        const secretString = {
            username: dbusername,
            password: userPassword,
            engine: 'mysql',
            port: PORT,
            dbname: dbname,
            dbClusterIdentifier: 'proxy',
        };
        const createSecretResponse = await secretsmanager.send(new client_secrets_manager_1.CreateSecretCommand({
            Name: secretName,
            Description: secretDescription,
            SecretString: JSON.stringify(secretString),
            Tags: [{ Key: 'Tenant', Value: tenantId }],
        }));
        // RDS Proxy 인증 정보 업데이트
        await updateRDSProxy(dbusername, createSecretResponse.ARN);
    }
    catch (error) {
        console.error(`Error creating user or schema for tenant ${tenantId}: ${error}`);
        throw new Error(`Error creating user or schema for tenant ${tenantId}: ${error}`);
    }
}
// RDS Proxy 인증 정보 업데이트
async function updateRDSProxy(dbusername, secretArn) {
    try {
        await rds
            .modifyDBProxy({
            DBProxyName: PROXY_NAME,
            Auth: [
                {
                    SecretArn: secretArn,
                    IAMAuth: 'REQUIRED',
                },
            ],
        })
            .promise();
    }
    catch (error) {
        console.error(`Error updating RDS Proxy for ${dbusername}: ${error}`);
        throw new Error(`Error updating RDS Proxy for ${dbusername}: ${error}`);
    }
}
// 비밀번호 생성 함수
function generatePassword(length) {
    return Array.from({ length }, () => alphabet.charAt(Math.floor(Math.random() * alphabet.length))).join('');
}
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiaW5kZXguanMiLCJzb3VyY2VSb290IjoiIiwic291cmNlcyI6WyJpbmRleC50cyJdLCJuYW1lcyI6W10sIm1hcHBpbmdzIjoiO0FBQUEscUZBQXFGO0FBQ3JGLGlHQUFpRzs7O0FBRWpHLCtDQUErQztBQUMvQyw2REFBNkQ7QUFFN0QsNENBQTRDO0FBQzVDLCtDQUErQztBQUMvQyxpREFBaUQ7QUFDakQsOENBQThDO0FBRTlDLFVBQVU7QUFDViw2QkFBNkI7QUFDN0Isc0RBQXNEO0FBQ3RELDJEQUEyRDtBQUMzRCxTQUFTO0FBQ1QsNERBQTREO0FBRTVELHlCQUF5QjtBQUN6QixvQkFBb0I7QUFDcEIsd0RBQXdEO0FBQ3hELCtEQUErRDtBQUMvRCxrREFBa0Q7QUFDbEQsd0RBQXdEO0FBQ3hELFdBQVc7QUFDWCxpRUFBaUU7QUFDakUsb0RBQW9EO0FBQ3BELG1DQUFtQztBQUNuQyxXQUFXO0FBQ1gsU0FBUztBQUVULDhCQUE4QjtBQUM5QixvREFBb0Q7QUFDcEQsaUNBQWlDO0FBQ2pDLDhCQUE4QjtBQUM5QixrQkFBa0I7QUFDbEIsNkJBQTZCO0FBQzdCLFVBQVU7QUFFVix5Q0FBeUM7QUFFekMsZUFBZTtBQUNmLHlCQUF5QjtBQUN6QixvRkFBb0Y7QUFDcEYsU0FBUztBQUNULHNCQUFzQjtBQUN0QixzREFBc0Q7QUFDdEQsZUFBZTtBQUNmLHlCQUF5QjtBQUN6QiwyRUFBMkU7QUFDM0UsU0FBUztBQUNULE1BQU07QUFDTixLQUFLO0FBR0wsK0JBQStCO0FBQy9CLHdDQUF3QztBQUN4Qyw0RUFBbUg7QUFFbkgsTUFBTSxjQUFjLEdBQUcsSUFBSSw2Q0FBb0IsQ0FBQyxFQUFFLE1BQU0sRUFBRSxPQUFPLENBQUMsR0FBRyxDQUFDLE1BQU0sRUFBRSxDQUFDLENBQUM7QUFDaEYsTUFBTSxHQUFHLEdBQUcsSUFBSSxHQUFHLENBQUMsR0FBRyxFQUFFLENBQUM7QUFDMUIsTUFBTSxRQUFRLEdBQUcsT0FBTyxDQUFDLEdBQUcsQ0FBQyxXQUFxQixDQUFDO0FBQ25ELE1B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QUFPLENBQUMsS0FBSyxDQUFDLDRDQUE0QyxRQUFRLEtBQUssS0FBSyxFQUFFLENBQUMsQ0FBQztRQUNoRixNQUFNLElBQUksS0FBSyxDQUFDLDRDQUE0QyxRQUFRLEtBQUssS0FBSyxFQUFFLENBQUMsQ0FBQztLQUNuRjtBQUNILENBQUM7QUFFRCx1QkFBdUI7QUFDdkIsS0FBSyxVQUFVLGNBQWMsQ0FBQyxVQUFrQixFQUFFLFNBQWtCO0lBQ2xFLElBQUk7UUFDRixNQUFNLEdBQUc7YUFDTixhQUFhLENBQUM7WUFDYixXQUFXLEVBQUUsVUFBVTtZQUN2QixJQUFJLEVBQUU7Z0JBQ0o7b0JBQ0UsU0FBUyxFQUFFLFNBQVM7b0JBQ3BCLE9BQU8sRUFBRSxVQUFVO2lCQUNwQjthQUNGO1NBQ0YsQ0FBQzthQUNELE9BQU8sRUFBRSxDQUFDO0tBQ2Q7SUFBQyxPQUFPLEtBQUssRUFBRTtRQUNkLE9BQU8sQ0FBQyxLQUFLLENBQUMsZ0NBQWdDLFVBQVUsS0FBSyxLQUFLLEVBQUUsQ0FBQyxDQUFDO1FBQ3RFLE1BQU0sSUFBSSxLQUFLLENBQUMsZ0NBQWdDLFVBQVUsS0FBSyxLQUFLLEVBQUUsQ0FBQyxDQUFDO0tBQ3pFO0FBQ0gsQ0FBQztBQUVELGFBQWE7QUFDYixTQUFTLGdCQUFnQixDQUFDLE1BQWM7SUFDdEMsT0FBTyxLQUFLLENBQUMsSUFBSSxDQUFDLEVBQUUsTUFBTSxFQUFFLEVBQUUsR0FBRyxFQUFFLENBQUMsUUFBUSxDQUFDLE1BQU0sQ0FBQyxJQUFJLENBQUMsS0FBSyxDQUFDLElBQUksQ0FBQyxNQUFNLEVBQUUsR0FBRyxRQUFRLENBQUMsTUFBTSxDQUFDLENBQUMsQ0FBQyxDQUFDLElBQUksQ0FBQyxFQUFFLENBQUMsQ0FBQztBQUM3RyxDQUFDIiwic291cmNlc0NvbnRlbnQiOlsiLy8gaW1wb3J0IHsgUkRTRGF0YUNsaWVudCwgRXhlY3V0ZVN0YXRlbWVudENvbW1hbmQgfSBmcm9tICdAYXdzLXNkay9jbGllbnQtcmRzLWRhdGEnO1xuLy8gaW1wb3J0IHsgU2VjcmV0c01hbmFnZXJDbGllbnQsIEdldFNlY3JldFZhbHVlQ29tbWFuZCB9IGZyb20gJ0Bhd3Mtc2RrL2NsaWVudC1zZWNyZXRzLW1hbmFnZXInO1xuXG4vLyBjb25zdCByZHNEYXRhQ2xpZW50ID0gbmV3IFJEU0RhdGFDbGllbnQoe30pO1xuLy8gY29uc3Qgc2VjcmV0c01hbmFnZXJDbGllbnQgPSBuZXcgU2VjcmV0c01hbmFnZXJDbGllbnQoe30pO1xuXG4vLyBleHBvcnRzLmhhbmRsZXIgPSBhc3luYyAoZXZlbnQ6IGFueSkgPT4ge1xuLy8gICBjb25zdCB0ZW5hbnRJZCA9IGV2ZW50LnRlbmFudElkOyAvLyDthYzrhIztirggSURcbi8vICAgY29uc3QgZGJQcm94eUFybiA9IHByb2Nlc3MuZW52LkRCX1BST1hZX0FSTjtcbi8vICAgY29uc3Qgc2VjcmV0QXJuID0gcHJvY2Vzcy5lbnYuU0VDUkVUX0FSTjtcblxuLy8gICB0cnkge1xuLy8gICAgIC8vIFJEUyDruYTrsIDsl5DshJwg7J6Q6rKpIOymneuqheydhCDqsIDsoLjsmLRcbi8vICAgICBjb25zdCBzZWNyZXQgPSBhd2FpdCBzZWNyZXRzTWFuYWdlckNsaWVudC5zZW5kKFxuLy8gICAgICAgbmV3IEdldFNlY3JldFZhbHVlQ29tbWFuZCh7IFNlY3JldElkOiBzZWNyZXRBcm4gfSlcbi8vICAgICApO1xuLy8gICAgIGNvbnN0IGNyZWRlbnRpYWxzID0gSlNPTi5wYXJzZShzZWNyZXQuU2VjcmV0U3RyaW5nISk7XG5cbi8vICAgICAvLyDthYzrhIztirjsnZgg7Iqk7YKk66eIIOyDneyEsSBTUUxcbi8vICAgICBjb25zdCBzcWwgPSBgXG4vLyAgICAgICBDUkVBVEUgU0NIRU1BIElGIE5PVCBFWElTVFMgdGVuYW50XyR7dGVuYW50SWR9O1xuLy8gICAgICAgQ1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgdGVuYW50XyR7dGVuYW50SWR9Lk9yZGVycyAoXG4vLyAgICAgICAgIG9yZGVySWQgSU5UIFBSSU1BUlkgS0VZIEFVVE9fSU5DUkVNRU5ULFxuLy8gICAgICAgICBvcmRlckRhdGUgVElNRVNUQU1QIERFRkFVTFQgQ1VSUkVOVF9USU1FU1RBTVBcbi8vICAgICAgICk7XG4vLyAgICAgICBDUkVBVEUgVEFCTEUgSUYgTk9UIEVYSVNUUyB0ZW5hbnRfJHt0ZW5hbnRJZH0uUHJvZHVjdHMgKFxuLy8gICAgICAgICBwcm9kdWN0SWQgSU5UIFBSSU1BUlkgS0VZIEFVVE9fSU5DUkVNRU5ULFxuLy8gICAgICAgICBwcm9kdWN0TmFtZSBWQVJDSEFSKDI1NSlcbi8vICAgICAgICk7XG4vLyAgICAgYDtcblxuLy8gICAgIC8vIFJEUyBQcm94eeulvCDthrXtlbQgU1FMIOyLpO2WiVxuLy8gICAgIGNvbnN0IGNvbW1hbmQgPSBuZXcgRXhlY3V0ZVN0YXRlbWVudENvbW1hbmQoe1xuLy8gICAgICAgcmVzb3VyY2VBcm46IGRiUHJveHlBcm4sXG4vLyAgICAgICBzZWNyZXRBcm46IHNlY3JldEFybixcbi8vICAgICAgIHNxbDogc3FsLFxuLy8gICAgICAgZGF0YWJhc2U6ICdtYWluX2RiJyxcbi8vICAgICB9KTtcblxuLy8gICAgIGF3YWl0IHJkc0RhdGFDbGllbnQuc2VuZChjb21tYW5kKTtcblxuLy8gICAgIHJldHVybiB7XG4vLyAgICAgICBzdGF0dXNDb2RlOiAyMDAsXG4vLyAgICAgICBib2R5OiBKU09OLnN0cmluZ2lmeSh7IG1lc3NhZ2U6IGBTY2hlbWEgY3JlYXRlZCBmb3IgdGVuYW50XyR7dGVuYW50SWR9YCB9KSxcbi8vICAgICB9O1xuLy8gICB9IGNhdGNoIChlcnJvcikge1xuLy8gICAgIGNvbnNvbGUuZXJyb3IoJ0Vycm9yIGNyZWF0aW5nIHNjaGVtYTonLCBlcnJvcik7XG4vLyAgICAgcmV0dXJuIHtcbi8vICAgICAgIHN0YXR1c0NvZGU6IDUwMCxcbi8vICAgICAgIGJvZHk6IEpTT04uc3RyaW5naWZ5KHsgbWVzc2FnZTogJ0Vycm9yIGNyZWF0aW5nIHNjaGVtYScsIGVycm9yIH0pLFxuLy8gICAgIH07XG4vLyAgIH1cbi8vIH07XG5cblxuaW1wb3J0ICogYXMgQVdTIGZyb20gJ2F3cy1zZGsnO1xuaW1wb3J0ICogYXMgbXlzcWwgZnJvbSAnbXlzcWwyL3Byb21pc2UnO1xuaW1wb3J0IHsgU2VjcmV0c01hbmFnZXJDbGllbnQsIEdldFNlY3JldFZhbHVlQ29tbWFuZCwgQ3JlYXRlU2VjcmV0Q29tbWFuZCB9IGZyb20gJ0Bhd3Mtc2RrL2NsaWVudC1zZWNyZXRzLW1hbmFnZXInO1xuXG5jb25zdCBzZWNyZXRzbWFuYWdlciA9IG5ldyBTZWNyZXRzTWFuYWdlckNsaWVudCh7IHJlZ2lvbjogcHJvY2Vzcy5lbnYuUkVHSU9OIH0pO1xuY29uc3QgcmRzID0gbmV3IEFXUy5SRFMoKTtcbmNvbnN0IEVORFBPSU5UID0gcHJvY2Vzcy5lbnYuREJfRU5EUE9JTlQgYXMgc3RyaW5nO1xuY29uc3QgUE9SVCA9IDMzMDY7XG5jb25zdCBVU1IgPSBwcm9jZXNzLmVudi5VU0VSIGFzIHN0cmluZztcbi8vIGNvbnN0IFJFR0lPTiA9IHByb2Nlc3MuZW52LlJFR0lPTiBhcyBzdHJpbmc7XG5jb25zdCBEQk5BTUUgPSBwcm9jZXNzLmVudi5EQl9OQU1FIGFzIHN0cmluZztcbmNvbnN0IFBST1hZX05BTUUgPSBwcm9jZXNzLmVudi5EQl9QUk9YWV9OQU1FIGFzIHN0cmluZztcbmNvbnN0IHNlY3JldEFybiA9IHByb2Nlc3MuZW52LkRCX1NFQ1JFVF9BUk4gYXMgc3RyaW5nO1xuY29uc3QgYWxwaGFiZXQgPSAnQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODknO1xuXG4vLyBMYW1iZGEg7ZW465Ok65+sXG5leHBvcnQgY29uc3QgaGFuZGxlciA9IGFzeW5jIChldmVudDogYW55KSA9PiB7XG4gIGNvbnN0IHRlbmFudElkID0gZXZlbnQudGVuYW50SWQ7IC8vIO2FjOuEjO2KuCBJROuKlCDsnbTrsqTtirjroZzrtoDthLAg7KCE64usXG4gIGlmICghdGVuYW50SWQpIHtcbiAgICB0aHJvdyBuZXcgRXJyb3IoJ1RlbmFudCBJRCBpcyByZXF1aXJlZCcpO1xuICB9XG5cbiAgbGV0IGNvbm5lY3Rpb247XG5cbiAgdHJ5IHtcbiAgICAvLyBTZWNyZXRzIE1hbmFnZXLsl5DshJwgREIg67mE67CA67KI7Zi4IOqwgOyguOyYpOq4sFxuICAgIGNvbnN0IHNlY3JldFZhbHVlID0gYXdhaXQgc2VjcmV0c21hbmFnZXIuc2VuZChuZXcgR2V0U2VjcmV0VmFsdWVDb21tYW5kKHsgU2VjcmV0SWQ6IHNlY3JldEFybiB9KSk7XG4gICAgY29uc3Qgc2VjcmV0RGF0YSA9IEpTT04ucGFyc2Uoc2VjcmV0VmFsdWUuU2VjcmV0U3RyaW5nIHx8ICd7fScpO1xuICAgIGNvbnN0IGRiUGFzc3dvcmQgPSBzZWNyZXREYXRhLnBhc3N3b3JkO1xuXG4gICAgLy8gTXlTUUwg7Jew6rKwIOyEpOyglVxuICAgIGNvbm5lY3Rpb24gPSBhd2FpdCBteXNxbC5jcmVhdGVDb25uZWN0aW9uKHtcbiAgICAgIGhvc3Q6IEVORFBPSU5ULFxuICAgICAgdXNlcjogVVNSLFxuICAgICAgcGFzc3dvcmQ6IGRiUGFzc3dvcmQsXG4gICAgICBwb3J0OiBQT1JULFxuICAgICAgZGF0YWJhc2U6IERCTkFNRSxcbiAgICB9KTtcblxuICAgIC8vIO2FjOuEjO2KuOyXkCDrjIDtlZwg642w7J207YSw67Kg7J207Iqk6rCAIOydtOuvuCDsobTsnqztlZjripTsp4Ag7ZmV7J24XG4gICAgY29uc3QgZGJOYW1lID0gYHRlbmFudF8ke3RlbmFudElkfV9kYmA7XG4gICAgY29uc3QgZGJDaGVja1F1ZXJ5ID0gYFNIT1cgREFUQUJBU0VTIExJS0UgJyR7ZGJOYW1lfSdgO1xuICAgIGNvbnN0IFtkYkNoZWNrUmVzdWx0XSA9IGF3YWl0IGNvbm5lY3Rpb24ucXVlcnkoZGJDaGVja1F1ZXJ5KTtcblxuICAgIGlmIChBcnJheS5pc0FycmF5KGRiQ2hlY2tSZXN1bHQpICYmIGRiQ2hlY2tRdWVyeS5sZW5ndGggPT09IDApIHtcbiAgICAgIGNvbnNvbGUubG9nKGBEYXRhYmFzZSBmb3IgdGVuYW50ICR7dGVuYW50SWR9IGRvZXMgbm90IGV4aXN0LiBDcmVhdGluZyBub3cuLi5gKTtcblxuICAgICAgLy8g642w7J207YSw67Kg7J207IqkIOuwjyDthYzsnbTruJQg7IOd7ISxXG4gICAgICBhd2FpdCBjcmVhdGVUZW5hbnREYXRhYmFzZUFuZFRhYmxlcyhjb25uZWN0aW9uLCB0ZW5hbnRJZCwgZGJQYXNzd29yZCk7XG4gICAgfSBlbHNlIHtcbiAgICAgIGNvbnNvbGUubG9nKGBEYXRhYmFzZSBmb3IgdGVuYW50ICR7dGVuYW50SWR9IGFscmVhZHkgZXhpc3RzLiBTa2lwcGluZyBjcmVhdGlvbi5gKTtcbiAgICB9XG5cbiAgICBjb25zb2xlLmxvZygnU3VjY2VzcycpO1xuICB9IGNhdGNoIChlcnJvcikge1xuICAgIGNvbnNvbGUuZXJyb3IoYEVycm9yOiAke2Vycm9yfWApO1xuICAgIHRocm93IG5ldyBFcnJvcihgRGF0YWJhc2UgY29ubmVjdGlvbiBvciBzY2hlbWEgY3JlYXRpb24gZmFpbGVkIGR1ZSB0byAke2Vycm9yfWApO1xuICB9IGZpbmFsbHkge1xuICAgIGlmIChjb25uZWN0aW9uKSB7XG4gICAgICBhd2FpdCBjb25uZWN0aW9uLmVuZCgpO1xuICAgIH1cbiAgfVxufTtcblxuLy8g7YWM64SM7Yq47JeQIOuMgO2VnCDrjbDsnbTthLDrsqDsnbTsiqQg67CPIO2FjOydtOu4lCDsg53shLFcbmFzeW5jIGZ1bmN0aW9uIGNyZWF0ZVRlbmFudERhdGFiYXNlQW5kVGFibGVzKGNvbm5lY3Rpb246IG15c3FsLkNvbm5lY3Rpb24sIHRlbmFudElkOiBzdHJpbmcsIGRiUGFzc3dvcmQ6IHN0cmluZykge1xuICBjb25zdCBkYnVzZXJuYW1lID0gYHVzZXJfJHt0ZW5hbnRJZH1gO1xuICBjb25zdCBkYm5hbWUgPSBgdGVuYW50XyR7dGVuYW50SWR9X2RiYDtcbiAgY29uc3QgdXNlclBhc3N3b3JkID0gZ2VuZXJhdGVQYXNzd29yZCgzMik7XG5cbiAgdHJ5IHtcbiAgICAvLyDsgqzsmqnsnpAg67CPIOyKpO2CpOuniCDsg53shLEg7L+866asIOyLpO2WiVxuICAgIGNvbnN0IHF1ZXJpZXMgPSBbXG4gICAgICBgQ1JFQVRFIFVTRVIgJyR7ZGJ1c2VybmFtZX0nIElERU5USUZJRUQgQlkgJyR7dXNlclBhc3N3b3JkfSc7YCxcbiAgICAgIGBDUkVBVEUgREFUQUJBU0UgJHtkYm5hbWV9O2AsXG4gICAgICBgR1JBTlQgQ1JFQVRFIFZJRVcsIFNIT1cgVklFVywgU0VMRUNULCBJTlNFUlQsIFVQREFURSBPTiAke2RibmFtZX0uKiBUTyAnJHtkYnVzZXJuYW1lfSc7YCxcbiAgICAgIGBVU0UgJHtkYm5hbWV9YCxcbiAgICAgIGBDUkVBVEUgVEFCTEUgb3JkZXJzIChcbiAgICAgICAgb3JkZXJfaWQgSU5UIEFVVE9fSU5DUkVNRU5UIFBSSU1BUlkgS0VZLFxuICAgICAgICBwcm9kdWN0X2lkIElOVCxcbiAgICAgICAgcXVhbnRpdHkgSU5ULFxuICAgICAgICB0b3RhbF9wcmljZSBERUNJTUFMKDEwLCAyKVxuICAgICAgKTtgLFxuICAgICAgYENSRUFURSBUQUJMRSBwcm9kdWN0cyAoXG4gICAgICAgIHByb2R1Y3RfaWQgSU5UIEFVVE9fSU5DUkVNRU5UIFBSSU1BUlkgS0VZLFxuICAgICAgICBwcm9kdWN0X25hbWUgVkFSQ0hBUigyNTUpLFxuICAgICAgICBwcm9kdWN0X2Rlc2NyaXB0aW9uIFRFWFQsXG4gICAgICAgIHByaWNlIERFQ0lNQUwoMTAsIDIpXG4gICAgICApO2AsXG4gICAgXTtcblxuICAgIGZvciAoY29uc3QgcXVlcnkgb2YgcXVlcmllcykge1xuICAgICAgYXdhaXQgY29ubmVjdGlvbi5xdWVyeShxdWVyeSk7XG4gICAgfVxuXG4gICAgLy8gU2VjcmV0cyBNYW5hZ2Vy7JeQIOyCrOyaqeyekCDruYTrsIAg7KCA7J6lXG4gICAgY29uc3Qgc2VjcmV0TmFtZSA9IGBBbWF6b25fcmRzX3Byb3h5X211bHRpdGVuYW50LyR7dGVuYW50SWR9X3VzZXJfc2VjcmV0YDtcbiAgICBjb25zdCBzZWNyZXREZXNjcmlwdGlvbiA9IGBQcm94eSBzZWNyZXQgY3JlYXRlZCBmb3IgdGVuYW50ICR7dGVuYW50SWR9YDtcbiAgICBjb25zdCBzZWNyZXRTdHJpbmcgPSB7XG4gICAgICB1c2VybmFtZTogZGJ1c2VybmFtZSxcbiAgICAgIHBhc3N3b3JkOiB1c2VyUGFzc3dvcmQsXG4gICAgICBlbmdpbmU6ICdteXNxbCcsXG4gICAgICBwb3J0OiBQT1JULFxuICAgICAgZGJuYW1lOiBkYm5hbWUsXG4gICAgICBkYkNsdXN0ZXJJZGVudGlmaWVyOiAncHJveHknLFxuICAgIH07XG5cbiAgICBjb25zdCBjcmVhdGVTZWNyZXRSZXNwb25zZSA9IGF3YWl0IHNlY3JldHNtYW5hZ2VyLnNlbmQoXG4gICAgICBuZXcgQ3JlYXRlU2VjcmV0Q29tbWFuZCh7XG4gICAgICAgIE5hbWU6IHNlY3JldE5hbWUsXG4gICAgICAgIERlc2NyaXB0aW9uOiBzZWNyZXREZXNjcmlwdGlvbixcbiAgICAgICAgU2VjcmV0U3RyaW5nOiBKU09OLnN0cmluZ2lmeShzZWNyZXRTdHJpbmcpLFxuICAgICAgICBUYWdzOiBbeyBLZXk6ICdUZW5hbnQnLCBWYWx1ZTogdGVuYW50SWQgfV0sXG4gICAgICB9KVxuICAgICk7XG5cbiAgICAvLyBSRFMgUHJveHkg7J247KadIOygleuztCDsl4XrjbDsnbTtirhcbiAgICBhd2FpdCB1cGRhdGVSRFNQcm94eShkYnVzZXJuYW1lLCBjcmVhdGVTZWNyZXRSZXNwb25zZS5BUk4pO1xuICB9IGNhdGNoIChlcnJvcikge1xuICAgIGNvbnNvbGUuZXJyb3IoYEVycm9yIGNyZWF0aW5nIHVzZXIgb3Igc2NoZW1hIGZvciB0ZW5hbnQgJHt0ZW5hbnRJZH06ICR7ZXJyb3J9YCk7XG4gICAgdGhyb3cgbmV3IEVycm9yKGBFcnJvciBjcmVhdGluZyB1c2VyIG9yIHNjaGVtYSBmb3IgdGVuYW50ICR7dGVuYW50SWR9OiAke2Vycm9yfWApO1xuICB9XG59XG5cbi8vIFJEUyBQcm94eSDsnbjspp0g7KCV67O0IOyXheuNsOydtO2KuFxuYXN5bmMgZnVuY3Rpb24gdXBkYXRlUkRTUHJveHkoZGJ1c2VybmFtZTogc3RyaW5nLCBzZWNyZXRBcm4/OiBzdHJpbmcpIHtcbiAgdHJ5IHtcbiAgICBhd2FpdCByZHNcbiAgICAgIC5tb2RpZnlEQlByb3h5KHtcbiAgICAgICAgREJQcm94eU5hbWU6IFBST1hZX05BTUUsXG4gICAgICAgIEF1dGg6IFtcbiAgICAgICAgICB7XG4gICAgICAgICAgICBTZWNyZXRBcm46IHNlY3JldEFybixcbiAgICAgICAgICAgIElBTUF1dGg6ICdSRVFVSVJFRCcsXG4gICAgICAgICAgfSxcbiAgICAgICAgXSxcbiAgICAgIH0pXG4gICAgICAucHJvbWlzZSgpO1xuICB9IGNhdGNoIChlcnJvcikge1xuICAgIGNvbnNvbGUuZXJyb3IoYEVycm9yIHVwZGF0aW5nIFJEUyBQcm94eSBmb3IgJHtkYnVzZXJuYW1lfTogJHtlcnJvcn1gKTtcbiAgICB0aHJvdyBuZXcgRXJyb3IoYEVycm9yIHVwZGF0aW5nIFJEUyBQcm94eSBmb3IgJHtkYnVzZXJuYW1lfTogJHtlcnJvcn1gKTtcbiAgfVxufVxuXG4vLyDruYTrsIDrsojtmLgg7IOd7ISxIO2VqOyImFxuZnVuY3Rpb24gZ2VuZXJhdGVQYXNzd29yZChsZW5ndGg6IG51bWJlcikge1xuICByZXR1cm4gQXJyYXkuZnJvbSh7IGxlbmd0aCB9LCAoKSA9PiBhbHBoYWJldC5jaGFyQXQoTWF0aC5mbG9vcihNYXRoLnJhbmRvbSgpICogYWxwaGFiZXQubGVuZ3RoKSkpLmpvaW4oJycpO1xufVxuIl19