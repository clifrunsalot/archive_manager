import unittest

from archive_manager.retrieval.query_planner import plan_query


class QueryPlannerTest(unittest.TestCase):
    def test_extract_label_values_request_is_deterministic(self):
        plan = plan_query("Extract all of the labels and their value in lesson.pdf")

        self.assertEqual(plan.intent, "label_values_inventory")
        self.assertFalse(plan.requires_llm)

    def test_precise_date_intents_are_deterministic(self):
        self.assertEqual(plan_query("total charges on Sep 12 2024").intent, "total_charges")
        self.assertEqual(
            plan_query("generate the total charges of each car service in the archive?").intent,
            "total_charges_inventory",
        )
        self.assertEqual(
            plan_query("State the total charges/costs per service date").intent,
            "total_charges_inventory",
        )
        self.assertEqual(
            plan_query("From oldest to recent car service events, state the costs of car service").intent,
            "total_charges_inventory",
        )
        self.assertEqual(
            plan_query("From oldest to recent car service events, state each of the services actually performed").intent,
            "performed_services_inventory",
        )

    def test_total_charges_inventory_recognizes_plural_and_grand_total_phrasing(self):
        plan = plan_query("Calculate the total cost of services for all recorded records?")
        self.assertEqual(plan.intent, "total_charges_inventory")
        self.assertFalse(plan.requires_llm)

        plan_with_sum = plan_query(
            "Generate a report of services and cost for each service date. Then add them up for a total combined cost"
        )
        self.assertEqual(plan_with_sum.intent, "total_charges_inventory")
        self.assertTrue(plan_with_sum.include_grand_total)

        plan_without_sum = plan_query("generate the total charges of each car service in the archive?")
        self.assertFalse(plan_without_sum.include_grand_total)
        self.assertEqual(
            plan_query("services actually performed by date in an ascii table").output_format,
            "ascii_table",
        )
        self.assertEqual(
            plan_query(
                "Generate a 3-column summary table of the services performed on each date: date, summary, cost."
            ).output_format,
            "markdown_table",
        )
        self.assertEqual(plan_query("repairs performed on 11/25/2025").intent, "performed_services")
        self.assertFalse(plan_query("repairs performed on 11/25/2025").requires_llm)

    def test_diagram_requests_select_the_matching_output_format(self):
        self.assertEqual(
            plan_query("Show a flowchart of the services performed on each date").output_format,
            "flowchart",
        )
        self.assertEqual(
            plan_query("Draw a process diagram of the services performed on each date").output_format,
            "flowchart",
        )
        self.assertEqual(
            plan_query("Generate a sequence diagram of the services performed on each date").output_format,
            "sequence_diagram",
        )
        self.assertEqual(
            plan_query("Generate a component diagram of the services performed on each date").output_format,
            "component_diagram",
        )

    def test_plan_exposes_scope_grouping_sort_and_fields(self):
        plan = plan_query("services not performed, order by cost lowest to highest")

        self.assertEqual(plan.scope, "all_events")
        self.assertEqual(plan.group_by, "service_date")
        self.assertEqual(plan.sort_direction, "ascending")
        self.assertEqual(plan.requested_fields, ("services_not_performed",))

    def test_inventory_and_summary_intents(self):
        self.assertEqual(plan_query("list processed files").intent, "source_inventory")
        self.assertEqual(
            plan_query("Who was the service advisor on each car service date?").intent,
            "service_advisor_inventory",
        )
        self.assertEqual(
            plan_query("What caused each repair problem?").intent,
            "repair_cause_inventory",
        )
        self.assertEqual(
            plan_query("display the following labels and their values: Promised and Ready").intent,
            "label_values_inventory",
        )
        self.assertEqual(
            plan_query("Examine each service file and list services not performed").intent,
            "not_performed_services_inventory",
        )
        self.assertTrue(plan_query("services not performed, order by cost lowest to highest").sort_by_cost)
        self.assertEqual(plan_query("What are the service dates saved in the archive?").intent, "service_date_inventory")
        self.assertEqual(plan_query("summarize every service record").intent, "multi_event_summary")

    def test_free_text_requires_llm(self):
        self.assertEqual(plan_query("What does the archive say about brakes?").intent, "free_text")
        self.assertTrue(plan_query("What does the archive say about brakes?").requires_llm)


if __name__ == "__main__":
    unittest.main()
