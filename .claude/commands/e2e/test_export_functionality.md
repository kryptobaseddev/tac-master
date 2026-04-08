# E2E Test: Export Functionality

Test table and query result export functionality in the Natural Language SQL Interface application.

## User Story

As a data analyst  
I want to export table data and query results as CSV files with one click  
So that I can analyze data in external tools and share results with colleagues

## Test Steps

1. Navigate to the `Application URL`
2. Take a screenshot of the initial state
3. **Verify** the page title is "Natural Language SQL Interface"
4. **Verify** core UI elements are present:
   - Query input textbox
   - Query button
   - Upload Data button
   - Available Tables section

5. Upload a test CSV file containing sample data
6. **Verify** the table appears in the Available Tables section
7. **Verify** a download button appears to the left of the 'x' icon for the table
8. Take a screenshot of the table with export button

9. Click the download button for the table
10. **Verify** a CSV file is downloaded with the table name
11. **Verify** the downloaded file contains the table data

12. Enter a query: "SELECT * FROM uploaded_table LIMIT 5"
13. Click the Query button
14. **Verify** the query results appear
15. **Verify** a download button appears to the left of the 'Hide' button
16. Take a screenshot of query results with export button

17. Click the download button for query results
18. **Verify** a CSV file is downloaded named "query_results.csv"
19. **Verify** the downloaded file contains the query results data

20. Execute an empty result query: "SELECT * FROM uploaded_table WHERE 1=0"
21. **Verify** the download button is still present
22. Click the download button
23. **Verify** an empty CSV with headers is downloaded

24. Take a screenshot of the final state

## Success Criteria
- Download buttons appear in correct positions (left of 'x' for tables, left of 'Hide' for results)
- Table export downloads complete table as CSV
- Query export downloads current results as CSV
- CSV files have appropriate names
- Empty results produce valid CSV with headers
- All download operations complete successfully
- 4 screenshots are taken