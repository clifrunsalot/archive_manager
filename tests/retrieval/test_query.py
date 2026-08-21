import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import archive_manager.retrieval.query as query


class QueryFilenameSearchTest(unittest.TestCase):
    def test_save_report_artifact_writes_markdown_report(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(query, "SAVE_REPORT_ARTIFACT", True), patch.object(query, "ARTIFACT_OUTPUT_DIR", Path(tmpdir)):
            report_path = query._save_report_artifact(
                "What is in report.pdf?",
                [{"payload": {"source": "report.pdf", "doc_id": "doc-1", "page": 1, "chunk_index": 0, "text": "Document text."}}],
                "The document says this.",
                "model",
                5,
            )

            self.assertIsNotNone(report_path)
            self.assertTrue(Path(report_path).is_file())
            self.assertIn("The document says this.", Path(report_path).read_text(encoding="utf-8"))

    def test_cli_saves_report_for_deterministic_answer(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(query, "answer", return_value="Deterministic answer"), patch.object(query, "SAVE_REPORT_ARTIFACT", False):
            with patch.object(query.sys, "argv", ["query.py", "--save-report", "--artifact-dir", tmpdir, "--question", "List labels"]):
                query.main()

            reports = list(Path(tmpdir).glob("report_*.md"))
            self.assertEqual(len(reports), 1)
            self.assertIn("Deterministic answer", reports[0].read_text(encoding="utf-8"))

    def test_generic_named_document_labels_group_repeated_values(self):
        values = query._extract_inline_label_values([
            "Started on: Saturday\nThe correct answer is: Aspirin\n"
            "The correct answer is: Liver\nDashboard / course: Quiz"
        ])

        self.assertEqual(values["Started on"], ["Saturday"])
        self.assertEqual(values["The correct answer is"], ["Aspirin", "Liver"])
        self.assertNotIn("Dashboard / course", values)

    def test_quiz_questions_pair_with_correct_answers(self):
        values = query._extract_inline_label_values([
            "25 26 27 Question 1 What is aspirin used for?\nCorrect\n"
            "a. Pain\nb. Sleep\nThe correct answer is: Pain\n"
            "Question 2 Which drug is an antihistamine?\nIncorrect\n"
            "The correct answer is: Chlorpheniramine"
        ])

        self.assertEqual(values, {
            "Question 1: What is aspirin used for?": ["Pain"],
            "Question 2: Which drug is an antihistamine?": ["Chlorpheniramine"],
        })

    def test_quiz_header_metadata_is_extracted(self):
        values = query._extract_inline_label_values([
            "Started on Saturday, September 27, 2025, 12:50 AM\n"
            "1 2 State Finished\n"
            "Completed on Saturday, September 27, 2025, 1:44 AM\n"
            "9 Time taken 53 mins 58 secs\n"
            "Points 25.00/30.00\n"
            "Grade 83.33 out of 100.00"
        ])

        self.assertEqual(values["Started on"], ["Saturday, September 27, 2025, 12:50 AM"])
        self.assertEqual(values["State"], ["Finished"])
        self.assertEqual(values["Completed on"], ["Saturday, September 27, 2025, 1:44 AM"])
        self.assertEqual(values["Time taken"], ["53 mins 58 secs"])
        self.assertEqual(values["Points"], ["25.00/30.00"])
        self.assertEqual(values["Grade"], ["83.33 out of 100.00"])

    def test_quiz_explanations_are_not_mistaken_for_labels(self):
        values = query._extract_inline_label_values([
            "Question 1 Which condition can lead to ED?\n"
            "The following conditions can lead to ED: prostatism and depression.\n"
            "The correct answer is: All options are correct"
        ])

        self.assertNotIn("The following conditions can lead to ED", values)
        self.assertEqual(values["Question 1: Which condition can lead to ED?"], ["All options are correct"])

    def test_named_label_formatter_can_omit_questions_and_deduplicate_metadata(self):
        result = query._format_named_label_values([
            ("quiz.pdf", {
                "Started on": ["Saturday, 12:50 AM", "Saturday, 12: 50 AM"],
                "Question 1: What?": ["Answer"],
            })
        ], "Extract labels. Do not include the questions.")

        self.assertIn("- Started on: Saturday, 12:50 AM", result)
        self.assertNotIn("Question 1", result)

    def test_source_inventory_request_is_detected(self):
        self.assertTrue(query._is_source_inventory_request("Generate the names of the files processed thus far"))
        self.assertFalse(query._is_source_inventory_request("What is in IMG_0944.png?"))

    def test_broad_query_requires_scope(self):
        self.assertTrue(query._is_broad_query("Tell me about the archive"))
        self.assertFalse(query._is_broad_query("What is in IMG_0944.png?"))

    def test_broad_query_does_not_call_embedding_or_llm(self):
        with patch.object(query, "ollama_embed_text") as embed, \
             patch.object(query, "ollama_chat") as chat:
            result = query.answer("Summarize everything")

        self.assertIn("Please narrow it", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_service_date_inventory_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=[
                "repair-2024-09-12-1.png",
                "repair-2023-05-08-1.png",
                "repair-2025-11-25-1.png",
            ]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            result = query.answer("What are the car service dates saved in the archive?")

        self.assertEqual(
            result,
            "Car service dates:\n- 2023-05-08\n- 2024-09-12\n- 2025-11-25",
        )
        embed.assert_not_called()
        chat.assert_not_called()

    def test_standard_date_formats_are_accepted(self):
        for question, expected in (
            ("service on 11/25/2025", "2025-11-25"),
            ("service on 25/11/2025", "2025-11-25"),
            ("service on 2025-11-25", "2025-11-25"),
            ("service on 25NOV25", "2025-11-25"),
            ("service on 25 November 2025", "2025-11-25"),
            ("service on Sep 12 2024", "2024-09-12"),
            ("service on September 12, 2024", "2024-09-12"),
        ):
            self.assertEqual(query._requested_service_date(question), expected)

    def test_month_first_date_scoped_service_request_is_deterministic(self):
        with (
            patch.object(query, "load_ingest_cache", return_value={"doc": "repair-2024-09-12-1.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = (
                "12SEP24\nB PERFORM MULTI POINT INSPECTION\n"
                "RECOMMENDED BUT NOT PERFORMED\nBR21 Brake Drums"
            )
            result = query.answer("What repairs were performed on Sep 12 2024?")

        self.assertIn("Perform Multi Point Inspection", result)
        self.assertNotIn("Brake Drums", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_date_scoped_total_charges_is_deterministic(self):
        with (
            patch.object(query, "load_ingest_cache", return_value={"doc": "repair-2024-09-12-1.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = (
                "12SEP24\nTOTAL CHARGES\n158.16\nPLEASE PAY\nTHIS AMOUNT\n168.64"
            )
            result = query.answer("What were the total charges for the car service on sep 12 2024?")

        self.assertEqual(result, "Total charges for 2024-09-12:\n- $158.16")
        embed.assert_not_called()
        chat.assert_not_called()

    def test_all_service_total_charges_are_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("generate the total charges of each car service in the archive?")

        self.assertEqual(result, "Car service total charges:\n- 2024-09-12: $158.16")
        embed.assert_not_called()
        chat.assert_not_called()

    def test_total_charges_inventory_grand_total_sums_all_dates(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["a.png", "b.png"]),
            patch.object(
                query,
                "_group_sources_into_records",
                return_value=[("invoice:1", ["a.png"]), ("invoice:2", ["b.png"])],
            ),
            patch.object(query, "load_ingest_cache", return_value={"doc": "a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(
                query,
                "extract_automotive_facts",
                side_effect=[
                    {"service_date": "08MAY23", "total_charges": "414.03"},
                    {"service_date": "12SEP24", "total_charges": "158.16"},
                ],
            ),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "text"
            result = query.answer(
                "Calculate the total cost of services for all recorded records? Add them up for a combined total."
            )

        self.assertIn("- 2023-05-08: $414.03", result)
        self.assertIn("- 2024-09-12: $158.16", result)
        self.assertIn("- Combined total: $572.19", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_date_scoped_label_values_are_not_archive_wide(self):
        result = query.answer("Show all of the labels and their corresponding values in the service record for 2023-05-08")

        self.assertIn("2023-05-08", result)
        self.assertIn("PROMISED", result)
        self.assertIn("READY", result)
        self.assertIn("PO NO: not found", result)
        self.assertIn("RATE: not found", result)
        self.assertIn("PAYMENT: not found", result)
        self.assertNotIn("2024-09-12", result)
        self.assertNotIn("2025-11-24", result)

    def test_date_scoped_label_values_match_candidate_service_dates(self):
        result = query.answer("Show all of the labels and their corresponding values in the service record for 2025-11-25")

        self.assertIn("Label values for 2025-11-25:", result)
        self.assertIn("PROMISED", result)
        self.assertIn("READY", result)

    def test_performed_services_inventory_is_grouped_by_date(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("From oldest to recent car service events, state each of the services actually performed")

        self.assertEqual(result, "Performed car services by date:\n2024-09-12:\n- Tire Rotation")
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_inventory_can_include_total_cost(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("services actually performed by date, include total cost")

        self.assertIn("- Total charges: $158.16", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_ascii_table_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("services actually performed by date in an ascii table include total cost")

        self.assertIn("+", result)
        self.assertIn("Tire Rotation", result)
        self.assertIn("$158.16", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_markdown_table_is_the_default_table_format(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer(
                "Generate a 3-column summary table of the services performed on each date: date, summary, cost."
            )

        self.assertIn("| Service Date | Services Performed | Total Charges |", result)
        self.assertIn("| --- | --- | --- |", result)
        self.assertIn("Tire Rotation", result)
        self.assertIn("$158.16", result)
        self.assertNotIn("+---", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_flowchart_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("Show a flowchart of the services performed on each date")

        self.assertIn("```mermaid", result)
        self.assertIn("flowchart TD", result)
        self.assertIn("Tire Rotation", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_sequence_diagram_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("Generate a sequence diagram of the services performed on each date")

        self.assertIn("```mermaid", result)
        self.assertIn("sequenceDiagram", result)
        self.assertIn("Vehicle->>Shop: Tire Rotation", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_performed_services_component_diagram_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_event_facts", return_value={}),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24", "total_charges": "158.16"}),
            patch.object(query, "extract_performed_services", return_value=["Tire Rotation"]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("Generate a component diagram of the services performed on each date")

        self.assertIn("```mermaid", result)
        self.assertIn("subgraph", result)
        self.assertIn("Tire Rotation", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_date_scoped_service_request_is_deterministic(self):
        with (
            patch.object(query, "load_ingest_cache", return_value={"doc": "repair-2024-09-12-1.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = (
                "12SEP24\nB PERFORM MULTI POINT INSPECTION\n"
                "C Tire Rotation and Synthetic Oil Change Special\n"
                "D REPLACE ENGINE AIR FILTER\n"
                "RECOMMENDED BUT NOT PERFORMED\nBR21 Brake Drums"
            )
            result = query.answer("What were the repairs performed for car service on 12 Sep 2024?")

        self.assertIn("Perform Multi Point Inspection", result)
        self.assertIn("Replace Engine Air Filter", result)
        self.assertNotIn("Brake Drums", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_numeric_date_scoped_service_request_is_deterministic(self):
        with (
            patch.object(query, "load_ingest_cache", return_value={"doc": "repair-2025-11-25-1.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = (
                "24NOV25\n25NOV25\nCLEAN AND ADJUST REAR DRUM BRAKES\n"
                "RECOMMENDED BUT NOT PERFORMED\nBR21 Brake Drums"
            )
            result = query.answer("What were the repairs performed for car service on 11/25/2025?")

        self.assertIn("Clean And Adjust Rear Drum Brakes", result)
        self.assertNotIn("Oil And Filter Change", result)
        self.assertNotIn("Brake Drums", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_multi_document_summary_uses_balanced_source_retrieval(self):
        sources = ["service_a.png", "service_b.png"]
        hits = [
            {"id": 1, "payload": {"source": "service_a.png", "page": 1, "chunk_index": 0, "text": "Date: 2024-01-01 Total: $10"}},
            {"id": 2, "payload": {"source": "service_b.png", "page": 1, "chunk_index": 0, "text": "Date: 2024-02-01 Total: $20"}},
        ]
        with (
            patch.object(query, "load_indexed_sources", return_value=sources),
            patch.object(
                query,
                "_group_sources_into_records",
                return_value=[("invoice:1", ["service_a.png"]), ("invoice:2", ["service_b.png"])],
            ),
            patch.object(
                query,
                "qdrant_search_by_source_embedding",
                side_effect=[[hits[0]], [hits[1]]],
            ),
            patch.object(query, "ollama_embed_text", return_value=[0.1]),
            patch.object(query, "ollama_chat", side_effect=["summary A", "summary B"]),
        ):
            result = query.answer("Summarize each car service record. Include total charges. Order by date")

        self.assertIn("invoice:1", result)
        self.assertIn("invoice:2", result)
        self.assertIn("summary A", result)
        self.assertIn("summary B", result)

    def test_source_inventory_returns_filenames_without_llm(self):
        with patch.object(query, "load_indexed_sources", return_value=["IMG_0944.png", "report.pdf"]), \
             patch.object(query, "ollama_embed_text") as embed, \
             patch.object(query, "ollama_chat") as chat:
            result = query.answer("Generate the names of the files processed thus far")

        self.assertEqual(result, "Processed files:\n- IMG_0944.png\n- report.pdf")
        embed.assert_not_called()
        chat.assert_not_called()

    def test_ollama_chat_passes_model_sampling_settings(self):
        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"message": {"content": "answer"}}

        with patch.dict(
            "os.environ",
            {
                "OLLAMA_TEMPERATURE": "0.1",
                "OLLAMA_SEED": "7",
                "OLLAMA_TOP_P": "0.25",
                "OLLAMA_TOP_K": "12",
                "OLLAMA_NUM_CTX": "8192",
            },
            clear=False,
        ), patch.object(query.REQUEST_SESSION, "post", return_value=Response()) as post:
            self.assertEqual(query.ollama_chat("model", []), "answer")

        options = post.call_args.kwargs["json"]["options"]
        self.assertEqual(options, {
            "temperature": 0.1,
            "seed": 7,
            "top_p": 0.25,
            "top_k": 12,
            "num_ctx": 8192,
        })

    def test_ollama_chat_falls_back_when_model_process_is_killed(self):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = payload.get("error", "")

            def raise_for_status(self):
                if self.status_code >= 400:
                    error = query.requests.HTTPError(f"HTTP {self.status_code}")
                    error.response = self
                    raise error

            def json(self):
                return self._payload

        with patch.object(
            query.REQUEST_SESSION,
            "post",
            side_effect=[
                Response(500, {"error": "llama-server process has terminated: signal: killed"}),
                Response(200, {"message": {"content": "fallback answer"}}),
            ],
        ) as post:
            result = query.ollama_chat("qwen2.5:14b", [{"role": "user", "content": "Question"}])

        self.assertEqual(result, "fallback answer")
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "qwen2.5-coder:7b")

    def test_source_inventory_filters_filenames_by_requested_date(self):
        with patch.object(
            query,
            "load_indexed_sources",
            return_value=[
                "repair-2023-05-08-1.png",
                "repair-2023-05-08-2.png",
                "repair-2024-09-12-1.png",
            ],
        ), patch.object(query, "ollama_embed_text") as embed, patch.object(query, "ollama_chat") as chat:
            result = query.answer("What are the names of the files associated with 2023-05-08?")

        self.assertEqual(
            result,
            "Processed files:\n- repair-2023-05-08-1.png\n- repair-2023-05-08-2.png",
        )
        embed.assert_not_called()
        chat.assert_not_called()

    def test_service_advisor_inventory_is_deterministic(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "12SEP24"}),
            patch.object(query, "extract_service_advisor", return_value="1277 KEVIN GOEHLE"),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "12SEP24"
            result = query.answer("Who was the service advisor on each car service date?")

        self.assertEqual(result, "Service advisors by date:\n- 2024-09-12: 1277 KEVIN GOEHLE")
        embed.assert_not_called()
        chat.assert_not_called()

    def test_repair_cause_inventory_filters_requested_date(self):
        with (
            patch.object(query, "load_indexed_sources", return_value=["service-a.png"]),
            patch.object(query, "_group_sources_into_records", return_value=[("invoice:1", ["service-a.png"])]),
            patch.object(query, "load_ingest_cache", return_value={"doc": "service-a.png"}),
            patch.object(query, "SEARCHABLE_DIR") as searchable_dir,
            patch.object(query, "extract_automotive_facts", return_value={"service_date": "24NOV25", "service_date_candidates": ["24NOV25", "25NOV25"]}),
            patch.object(query, "extract_service_causes", return_value=[("Brake Service", "WORN/RUSTED")]),
            patch.object(query, "ollama_embed_text") as embed,
            patch.object(query, "ollama_chat") as chat,
        ):
            searchable_dir.__truediv__.return_value.read_text.return_value = "24NOV25"
            result = query.answer("What caused the repairs on 11-25-2025?")

        self.assertIn("WORN/RUSTED", result)
        embed.assert_not_called()
        chat.assert_not_called()

    def test_filename_candidates_extract_supported_filename(self):
        self.assertEqual(
            query._filename_candidates("Summarize IMG_0944.png and report.pdf"),
            ["IMG_0944.png", "report.pdf"],
        )

    def test_named_source_candidates_support_spaces(self):
        with patch.object(query, "load_indexed_sources", return_value=["PHT LO 8_26_2025 Schedule.pdf"]):
            self.assertEqual(
                query._named_source_candidates("Extract labels from PHT LO 8_26_2025 Schedule.pdf"),
                ["PHT LO 8_26_2025 Schedule.pdf"],
            )

    def test_exact_filename_hits_are_prioritized(self):
        named = [{"id": 1, "payload": {"source": "IMG_0944.png"}}]
        semantic = [
            {"id": 2, "payload": {"source": "other.pdf"}},
            {"id": 1, "payload": {"source": "IMG_0944.png"}},
        ]
        merged = query._merge_hits(named, semantic, limit=2)
        self.assertEqual([hit["id"] for hit in merged], [1, 2])

    def test_filename_regex_candidates_extract_explicit_pattern(self):
        self.assertEqual(
            query._filename_regex_candidates("Find filename_regex=IMG_09[0-9]+\\.png"),
            [r"IMG_09[0-9]+\.png"],
        )

    def test_answer_queries_exact_filename_and_includes_source(self):
        named_hit = {
            "id": 1,
            "payload": {
                "source": "IMG_0944.png",
                "doc_id": "doc-1",
                "page": 1,
                "chunk_index": 0,
                "text": "The matching document text.",
            },
        }
        with patch.object(query, "ollama_embed_text", return_value=[0.1]), \
             patch.object(query, "qdrant_search", return_value={"result": []}), \
             patch.object(query, "qdrant_search_by_source", return_value=[named_hit]), \
             patch.object(query, "ollama_chat", return_value="The document says this.") as chat:
            result = query.answer("What is in IMG_0944.png?", top_k=5)

        self.assertEqual(result, "The document says this.")
        user_prompt = chat.call_args.args[1][1]["content"]
        self.assertIn("source=IMG_0944.png", user_prompt)


if __name__ == "__main__":
    unittest.main()