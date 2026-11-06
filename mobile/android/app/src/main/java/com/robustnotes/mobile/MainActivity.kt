package com.robustnotes.mobile

import android.os.Bundle
import android.util.AtomicFile
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.DateFormat
import java.util.Date

private data class LocalNote(
    val id: Long,
    val title: String,
    val body: String,
    val updated: Long,
)

private val Ink = Color(0xFF17212B)
private val MutedInk = Color(0xFF637381)
private val Canvas = Color(0xFFF4F7F9)
private val Accent = Color(0xFF15A9B8)
private val AccentSoft = Color(0xFFE5F7F8)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val noteStore = AtomicFile(File(filesDir, "notes.json"))
        val initialNotes = readNotes(noteStore)
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(
                    primary = Accent,
                    onPrimary = Color.White,
                    background = Canvas,
                    surface = Color.White,
                    onSurface = Ink,
                ),
            ) {
                Surface(modifier = Modifier.fillMaxSize(), color = Canvas) {
                    NotesApp(
                        initialNotes = initialNotes,
                        onPersist = { notes -> writeNotes(noteStore, notes) },
                    )
                }
            }
        }
    }

    private fun readNotes(store: AtomicFile): List<LocalNote> = runCatching {
        val raw = store.openRead().bufferedReader().use { it.readText() }
        val array = JSONArray(raw)
        (0 until array.length()).mapNotNull { index ->
            val item = array.optJSONObject(index) ?: return@mapNotNull null
            LocalNote(
                id = item.optLong("id"),
                title = item.optString("title"),
                body = item.optString("body"),
                updated = item.optLong("updated"),
            )
        }
    }.getOrDefault(emptyList())

    private fun writeNotes(store: AtomicFile, notes: List<LocalNote>): Boolean {
        val json = JSONArray().apply {
            notes.forEach { note ->
                put(JSONObject()
                    .put("id", note.id)
                    .put("title", note.title)
                    .put("body", note.body)
                    .put("updated", note.updated))
            }
        }.toString().toByteArray(Charsets.UTF_8)

        var stream: java.io.FileOutputStream? = null
        try {
            stream = store.startWrite()
            stream.write(json)
            store.finishWrite(stream)
            return true
        } catch (error: Exception) {
            stream?.let(store::failWrite)
            return false
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NotesApp(
    initialNotes: List<LocalNote>,
    onPersist: (List<LocalNote>) -> Boolean,
) {
    var notes by remember { mutableStateOf(initialNotes.sortedByDescending { it.updated }) }
    var search by rememberSaveable { mutableStateOf("") }
    var editingId by rememberSaveable { mutableStateOf<Long?>(null) }
    var draftId by rememberSaveable { mutableStateOf<Long?>(null) }
    var pendingDelete by remember { mutableStateOf<LocalNote?>(null) }
    var deleteFailed by remember { mutableStateOf(false) }
    val uriHandler = LocalUriHandler.current
    val editing = notes.firstOrNull { it.id == editingId }
    val draft = draftId?.let { LocalNote(it, "", "", System.currentTimeMillis()) }

    fun save(note: LocalNote): Boolean {
        val saved = note.copy(
            title = note.title.trim().ifBlank { "Untitled note" },
            updated = System.currentTimeMillis(),
        )
        val updatedNotes = (notes.filterNot { it.id == saved.id } + saved).sortedByDescending { it.updated }
        if (!onPersist(updatedNotes)) return false
        notes = updatedNotes
        editingId = null
        draftId = null
        return true
    }

    fun remove(note: LocalNote) {
        val updatedNotes = notes.filterNot { it.id == note.id }
        if (onPersist(updatedNotes)) {
            notes = updatedNotes
            pendingDelete = null
            editingId = null
            draftId = null
            deleteFailed = false
        } else {
            deleteFailed = true
        }
    }

    val activeNote = editing ?: draft
    if (activeNote != null) {
        NoteEditor(
            note = activeNote,
            isNew = editing == null,
            onBack = { changed ->
                if (editing == null && changed.title.isBlank() && changed.body.isBlank()) {
                    draftId = null
                } else {
                    save(changed)
                }
            },
            onSave = ::save,
            onDelete = { note -> pendingDelete = note },
        )
    } else {
        val query = search.trim()
        val visibleNotes = notes.filter { note ->
            query.isEmpty() || note.title.contains(query, ignoreCase = true) || note.body.contains(query, ignoreCase = true)
        }
        Scaffold(
            containerColor = Canvas,
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text("RobustNotes", fontWeight = FontWeight.Bold, color = Ink)
                            Text("YOUR PRIVATE NOTEBOOK", style = MaterialTheme.typography.labelSmall, color = MutedInk)
                        }
                    },
                    actions = {
                        TextButton(onClick = { uriHandler.openUri("https://robustnotes.com/privacy.html") }) {
                            Text("Privacy policy")
                        }
                    },
                    colors = TopAppBarDefaults.topAppBarColors(containerColor = Canvas),
                )
            },
            floatingActionButton = {
                Button(onClick = { draftId = System.nanoTime() }, shape = RoundedCornerShape(18.dp)) {
                    Text("＋  New note", fontWeight = FontWeight.SemiBold)
                }
            },
        ) { padding ->
            Column(
                modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp),
            ) {
                Column(modifier = Modifier.padding(top = 12.dp, bottom = 16.dp)) {
                    Text("A clearer space to think.", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = Ink)
                    Text("Your notes stay on this device.", modifier = Modifier.padding(top = 4.dp), color = MutedInk)
                }
                OutlinedTextField(
                    value = search,
                    onValueChange = { search = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    shape = RoundedCornerShape(16.dp),
                    placeholder = { Text("Search your notes") },
                    leadingIcon = { Text("⌕", color = Accent, style = MaterialTheme.typography.titleLarge) },
                )
                Row(
                    modifier = Modifier.fillMaxWidth().padding(top = 20.dp, bottom = 10.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Recent notes", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = Ink)
                    Text("${visibleNotes.size} ${if (visibleNotes.size == 1) "note" else "notes"}", color = MutedInk, style = MaterialTheme.typography.labelLarge)
                }

                if (visibleNotes.isEmpty()) {
                    EmptyNotesState(hasQuery = query.isNotEmpty(), onCreate = { draftId = System.nanoTime() })
                } else {
                    LazyColumn(
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        items(visibleNotes, key = { it.id }) { note ->
                            NoteCard(note = note, onClick = { editingId = note.id })
                        }
                        item { Spacer(Modifier.height(88.dp)) }
                    }
                }
            }
        }
    }

    pendingDelete?.let { note ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null; deleteFailed = false },
            title = { Text("Delete this note?") },
            text = {
                Column {
                    Text("“${note.title.ifBlank { "Untitled note" }}” will be removed from this device.")
                    if (deleteFailed) Text("Could not delete the note. Please try again.", color = Color(0xFFB3261E), modifier = Modifier.padding(top = 8.dp))
                }
            },
            confirmButton = {
                TextButton(onClick = { remove(note) }) { Text("Delete", color = Color(0xFFB3261E)) }
            },
            dismissButton = { TextButton(onClick = { pendingDelete = null; deleteFailed = false }) { Text("Keep note") } },
        )
    }
}

@Composable
private fun NoteCard(note: LocalNote, onClick: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = Color.White),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier.size(38.dp).background(AccentSoft, CircleShape),
                    contentAlignment = Alignment.Center,
                ) { Text("R", color = Accent, fontWeight = FontWeight.Bold) }
                Column(modifier = Modifier.weight(1f).padding(start = 12.dp)) {
                    Text(note.title.ifBlank { "Untitled note" }, style = MaterialTheme.typography.titleMedium, color = Ink, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(formatDate(note.updated), style = MaterialTheme.typography.labelSmall, color = MutedInk, modifier = Modifier.padding(top = 3.dp))
                }
            }
            Text(
                text = note.body.ifBlank { "Tap to start writing…" },
                modifier = Modifier.padding(top = 15.dp),
                color = if (note.body.isBlank()) MutedInk else Ink,
                style = MaterialTheme.typography.bodyMedium,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}

@Composable
private fun EmptyNotesState(hasQuery: Boolean, onCreate: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(top = 56.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(modifier = Modifier.size(76.dp).background(AccentSoft, CircleShape), contentAlignment = Alignment.Center) {
            Text("R", color = Accent, style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        }
        Text(
            if (hasQuery) "No matching notes" else "Start with a fresh page",
            modifier = Modifier.padding(top = 18.dp),
            style = MaterialTheme.typography.titleLarge,
            color = Ink,
            fontWeight = FontWeight.Bold,
        )
        Text(
            if (hasQuery) "Try another title or phrase." else "Capture an idea, a reminder, or anything worth keeping.",
            modifier = Modifier.padding(top = 7.dp),
            color = MutedInk,
        )
        if (!hasQuery) {
            Button(onClick = onCreate, modifier = Modifier.padding(top = 20.dp), shape = RoundedCornerShape(14.dp)) {
                Text("Create your first note")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NoteEditor(
    note: LocalNote,
    isNew: Boolean,
    onBack: (LocalNote) -> Unit,
    onSave: (LocalNote) -> Boolean,
    onDelete: (LocalNote) -> Unit,
) {
    var title by rememberSaveable(note.id) { mutableStateOf(note.title) }
    var body by rememberSaveable(note.id) { mutableStateOf(note.body) }
    var saveFailed by rememberSaveable(note.id) { mutableStateOf(false) }
    val currentNote = note.copy(title = title, body = body)
    BackHandler { onBack(currentNote) }
    Scaffold(
        containerColor = Canvas,
        topBar = {
            TopAppBar(
                title = { Text(if (isNew) "New note" else "Edit note", color = Ink, fontWeight = FontWeight.SemiBold) },
                navigationIcon = { TextButton(onClick = { onBack(currentNote) }) { Text("Back") } },
                actions = {
                    if (!isNew) TextButton(onClick = { onDelete(note) }) { Text("Delete", color = Color(0xFFB3261E)) }
                    TextButton(onClick = { saveFailed = !onSave(currentNote) }) { Text("Save", color = Accent, fontWeight = FontWeight.Bold) }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Canvas),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 20.dp, vertical = 12.dp),
        ) {
            Text("SAVED PRIVATELY ON THIS DEVICE", style = MaterialTheme.typography.labelSmall, color = Accent, fontWeight = FontWeight.Bold)
            if (saveFailed) Text("Could not save this note. Please try again.", color = Color(0xFFB3261E), modifier = Modifier.padding(top = 8.dp))
            OutlinedTextField(
                value = title,
                onValueChange = { title = it },
                modifier = Modifier.fillMaxWidth().padding(top = 14.dp),
                placeholder = { Text("Note title") },
                singleLine = true,
                textStyle = MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold, color = Ink),
                shape = RoundedCornerShape(14.dp),
            )
            OutlinedTextField(
                value = body,
                onValueChange = { body = it },
                modifier = Modifier.weight(1f).fillMaxWidth().padding(top = 12.dp, bottom = 12.dp),
                placeholder = { Text("Write something worth remembering…") },
                shape = RoundedCornerShape(16.dp),
            )
        }
    }
}

private fun formatDate(timestamp: Long): String =
    DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(timestamp))
