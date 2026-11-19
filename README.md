# Bot de gols do Brasileirão Série A

Bot em Python que recebe gols da **Live Football API** por webhooks (push) e publica mensagens diretamente no canal Telegram **@legendaskwkwkek**, prontas para copiar.

## Funcionalidades

- Notificação de cada gol recebido da Série A. Não precisa fazer consultas a cada segundo.
- Placar registrado no instante do gol, não necessariamente o resultado final.
- Assistência com primeiro nome e sobrenome quando confirmados pela API; caso contrário, omite para não inventar.
- Gols contra destacados como (GC) quando forem identificados pela fonte.
- Evita notificações repetidas com o mesmo placar de um mesmo jogo.
- Fila local SQLite e reenvio quando o Telegram falhar.
- Registra automaticamente o webhook da Série A quando as variáveis secretas estão configuradas.

## Mensagem de exemplo

🇧🇷 GOL DO CRUZEIRO!

⚽️ Jogador (GC)

🇧🇷 Cruzeiro 2 x 1 Palmeiras 🇧🇷

🏆 Brasileirão Série A

📸 Reprodução

🔔 Compartilhe e siga o canal!

## Como configurar no Render

1. Acrescente seu bot como **administrador do canal**, com permissão para enviar mensagens.
2. No Render, crie Web Service a partir deste repositório. Build: **pip install -r requirements.txt**. Start: **uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1 --no-access-log**.
3. No painel do serviço, em **Environment**, configure as variáveis secretas: **LFA_API_KEY**, **TELEGRAM_BOT_TOKEN**, **WEBHOOK_SECRET** (mínimo de 24 caracteres); mantenha **TELEGRAM_CHAT_ID=@legendaskwkwkek**.
4. O Render informa **RENDER_EXTERNAL_URL** automaticamente. Em outro host, configure PUBLIC_BASE_URL com uma URL pública HTTPS.
5. Abra **https://SEU-SERVICO.onrender.com/health**: quando todas as credenciais estiverem corretas, deverá mostrar **configured: true** e, após o registro, **webhook_registered: true**.

O endpoint para a Live Football API é protegido por segredo longo no caminho; não divulgue essa URL. Os tokens não estão no repositório e devem ser configurados somente nas variáveis secretas do Render.

**Como o token e a chave de API já foram enviados em uma conversa, é recomendável revogá-los e gerar novos valores** para uso em produção. Não publique os novos valores no GitHub nem em mensagens públicas.

## Latência e limitações

Webhooks são mais econômicos que consultar a API a cada segundo. Eles notificam quando o fornecedor detecta o gol, porém não existe garantia de chegada em 1 segundo. Cada webhook entregue custa um crédito, e consultas complementares a detalhes do jogo/perfil também usam créditos.

O Render **Free** não gera cobrança por si só, mas pode hibernar após inatividade e usa disco temporário. Por isso, o Free é apenas para testes: mensagens podem atrasar e a deduplicação pode se perder após reinício. Para alertas com baixa latência e histórico durável, hospede em serviço sempre ativo com armazenamento persistente; confirme preços e autorize eventuais cobranças antes de contratar.

Detalhes e preços: https://www.live-football-api.com/docs

## Desenvolvimento local

Instale dependências com **pip install -r requirements.txt**. Configure as variáveis de ambiente e inicie:

    uvicorn app:app --host 127.0.0.1 --port 8000

A aplicação não contém tokens reais, senhas nem credenciais de produção. O arquivo .env está no .gitignore.
