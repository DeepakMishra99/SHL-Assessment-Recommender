// --- 1. HEALTH STATUS POLLING ---
async function checkServerStatus() {
    const healthBadge = document.getElementById('health-badge');
    
    try {
       
        const res = await fetch('/health');
        if (res.ok) {
            const data = await res.json();
            
            // Fix: Check for both possible success signals
            if (data.db_ready === true || data.status === "ok") {
                healthBadge.className = 'badge online';
                healthBadge.innerHTML = '<div class="dot"></div> Server Ready';
            } else {
                healthBadge.className = 'badge';
                healthBadge.innerHTML = '<div class="dot"></div> Initializing DB...';
            }
        }
       
    } catch (error) {
        healthBadge.className = 'badge offline';
        healthBadge.innerHTML = '<div class="dot"></div> Server Offline';
    }
}

// Check immediately, then every 5 seconds
checkServerStatus();
setInterval(checkServerStatus, 5000);


// --- 2. ADVANCED CHAT & SESSION MANAGEMENT ---
const chatWindow = document.getElementById('chat-window');
const inputField = document.getElementById('user-input');
const historyList = document.getElementById('history-list');

// Load saved chats from the browser, or start an empty object
let savedChats = JSON.parse(localStorage.getItem('shl_chats')) || {};
let currentSessionId = null;

// Renders a single message to the screen
function renderMessage(role, content) {
    const msgDiv = document.createElement('div');
    // Ensure we use 'agent' class for CSS styling, even though backend calls it 'assistant'
    const cssClass = role === 'assistant' ? 'agent' : role; 
    msgDiv.className = `message ${cssClass}`;
    msgDiv.innerHTML = cssClass === 'agent' ? marked.parse(content) : content;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

// Updates the left sidebar with all saved chats
function updateSidebar() {
    historyList.innerHTML = ''; // Clear current list
    
    // Loop through all saved sessions in reverse chronological order
    const sessionIds = Object.keys(savedChats).reverse();
    
    sessionIds.forEach(id => {
        const session = savedChats[id];
        const historyItem = document.createElement('div');
        historyItem.className = 'history-item';
        historyItem.innerText = `💬 ${session.title}`;
        
        // When clicked, load this specific chat
        historyItem.addEventListener('click', () => loadChatSession(id));
        historyList.appendChild(historyItem);
    });
}

// Loads a specific chat into the main window
function loadChatSession(sessionId) {
    currentSessionId = sessionId;
    chatWindow.innerHTML = ''; // Clear window
    
    // Render all saved messages for this session
    savedChats[sessionId].messages.forEach(msg => {
        renderMessage(msg.role, msg.content);
    });
}

// Starts a completely fresh chat
function startNewChat() {
    currentSessionId = null;
    chatWindow.innerHTML = '<div class="message agent">Hello! I am connected to the SHL catalog. What job role are you recruiting for?</div>';
}


// --- 3. SENDING MESSAGES ---
async function sendMessage() {
    const text = inputField.value.trim();
    if (!text) return;

    // 1. If this is a brand new chat, create a new Session ID
    if (!currentSessionId) {
        currentSessionId = "session_" + Date.now();
        savedChats[currentSessionId] = {
            title: text.substring(0, 25) + "...", 
            messages: []
        };
    }

    // 2. Render user message and save to local storage
    renderMessage('user', text);
    inputField.value = '';
    
    // Store as 'user' for the backend
    savedChats[currentSessionId].messages.push({ role: 'user', content: text });
    localStorage.setItem('shl_chats', JSON.stringify(savedChats));
    updateSidebar();

    // 3. Show loading indicator
    const loadingId = "loading-" + Date.now();
    chatWindow.innerHTML += `<div class="message agent" id="${loadingId}">Searching catalog...</div>`;
    chatWindow.scrollTop = chatWindow.scrollHeight;

    // 4. Send to Python Backend
    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                // Send the FULL array of messages!
                messages: savedChats[currentSessionId].messages 
            })
        });

        document.getElementById(loadingId).remove();

        if (response.ok) {
            const data = await response.json();
            
            // Build the final display string using the reply AND the new recommendations array
            let finalContent = data.reply;
            
            if (data.recommendations && data.recommendations.length > 0) {
                finalContent += "\n\n### Recommendations:\n";
                // Format the array back into a markdown list/table for the UI
                data.recommendations.forEach(rec => {
                    finalContent += `* [${rec.name}](${rec.url}) *(Type: ${rec.test_type})*\n`;
                });
            }

            // Render agent reply and save to local storage
            renderMessage('assistant', finalContent);
            savedChats[currentSessionId].messages.push({ role: 'assistant', content: finalContent });
            localStorage.setItem('shl_chats', JSON.stringify(savedChats));
            
        } else {
            const err = await response.json();
            // Stringify the error so it doesn't say [object Object]
            const errorMessage = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
            renderMessage('error', `Server Error: ${errorMessage}`);
        }
    } catch (error) {
        document.getElementById(loadingId).remove();
        renderMessage('error', 'Connection failed. Please ensure the Python server is running.');
    }
}

// --- 4. EVENT LISTENERS & INITIALIZATION ---
document.getElementById('send-btn').addEventListener('click', sendMessage);

inputField.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

document.getElementById('new-chat-btn').addEventListener('click', startNewChat);

// Initialize sidebar and chat window on page load
updateSidebar();
startNewChat();