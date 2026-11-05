package com.robustnotes.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

private data class LocalNote(val id: Long, val title: String, val body: String, val updated: Long)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                NotesHome(
                    initialNotes = readNotes(),
                    onPersist = { saveNotes(it) },
                )
            }
        }
    }

    private fun readNotes(): List<LocalNote> = runCatching {
        val file = File(filesDir, "notes.json")
        if (!file.exists()) return@runCatching emptyList()
        val array = JSONArray(file.readText())
        (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            LocalNote(item.getLong("id"), item.optString("title"), item.optString("body"), item.optLong("updated"))
        }
    }.getOrDefault(emptyList())

    private fun saveNotes(notes: List<LocalNote>) {
        val array = JSONArray()
        notes.forEach { note ->
            array.put(JSONObject().put("id", note.id).put("title", note.title).put("body", note.body).put("updated", note.updated))
        }
        val target = File(filesDir, "notes.json")
        val temporary = File(filesDir, "notes.json.tmp")
        temporary.writeText(array.toString())
        if (!temporary.renameTo(target)) {
            target.writeText(array.toString())
            temporary.delete()
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun NotesHome(initialNotes: List<LocalNote>, onPersist: (List<LocalNote>) -> Unit) {
    var notes by remember { mutableStateOf(initialNotes) }
    var editing by remember { mutableStateOf<LocalNote?>(null) }
    Scaffold(
        topBar = { TopAppBar(title = { Text("RobustNotes") }) },
        floatingActionButton = {
            Button(onClick = { editing = LocalNote(System.currentTimeMillis(), "Untitled note", "", System.currentTimeMillis()) }) {
                Text("＋  New note")
            }
        },
    ) { padding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { Text("Your ideas, saved on this device", modifier = Modifier.padding(vertical = 14.dp), style = MaterialTheme.typography.titleMedium) }
            items(notes.sortedByDescending { it.updated }, key = { it.id }) { note ->
                Card(modifier = Modifier.fillMaxWidth().clickable { editing = note }) {
                    Column(modifier = Modifier.padding(18.dp)) {
                        Text(note.title.ifBlank { "Untitled note" }, style = MaterialTheme.typography.titleMedium)
                        Text(note.body.ifBlank { "Tap to start writing" }, maxLines = 3, modifier = Modifier.padding(top = 6.dp), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            if (notes.isEmpty()) item { Text("Create a note to get started.", modifier = Modifier.padding(top = 24.dp)) }
        }
    }
    editing?.let { current ->
        var title by remember(current.id) { mutableStateOf(current.title) }
        var body by remember(current.id) { mutableStateOf(current.body) }
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text("Edit note") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(value = title, onValueChange = { title = it }, label = { Text("Title") }, singleLine = true)
                    OutlinedTextField(value = body, onValueChange = { body = it }, label = { Text("Write a note") }, minLines = 8)
                }
            },
            confirmButton = {
                Row {
                    TextButton(onClick = {
                        notes = notes.filterNot { it.id == current.id }
                        onPersist(notes)
                        editing = null
                    }) { Text("Delete") }
                    Spacer(Modifier.padding(horizontal = 4.dp))
                    Button(onClick = {
                        val updated = current.copy(title = title, body = body, updated = System.currentTimeMillis())
                        notes = (notes.filterNot { it.id == current.id } + updated).sortedByDescending { it.updated }
                        onPersist(notes)
                        editing = null
                    }) { Text("Save") }
                }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text("Cancel") } },
        )
    }
}
