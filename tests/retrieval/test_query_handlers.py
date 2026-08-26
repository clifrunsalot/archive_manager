import unittest

from archive_manager.retrieval.query_handlers import QueryHandlerRegistry


class QueryHandlerRegistryTest(unittest.TestCase):
    def test_register_and_resolve_handler(self):
        registry = QueryHandlerRegistry()
        registry.register("example", lambda question, plan, run_id: "answer")

        self.assertEqual(registry.get("example")("", None, ""), "answer")
        self.assertEqual(registry.intents(), ("example",))

    def test_duplicate_registration_is_rejected(self):
        registry = QueryHandlerRegistry()
        handler = lambda question, plan, run_id: "answer"
        registry.register("example", handler)

        with self.assertRaises(ValueError):
            registry.register("example", handler)

    def test_default_deterministic_intents_are_registered(self):
        import archive_manager.retrieval.query as query

        self.assertEqual(
            query._deterministic_handlers().intents(),
            (
                "broad_scope",
                "document_date_inventory",
                "label_values_inventory",
                "not_performed_services_inventory",
                "performed_services",
                "performed_services_inventory",
                "quiz_question_inventory",
                "quiz_topic_source",
                "repair_cause_inventory",
                "service_advisor_inventory",
                "service_date_inventory",
                "source_by_doc_id",
                "source_inventory",
                "total_charges",
                "total_charges_inventory",
            ),
        )


if __name__ == "__main__":
    unittest.main()
