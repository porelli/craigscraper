import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime, timedelta
from urllib.parse import urlparse
import os

# Set page configuration
st.set_page_config(
    page_title="Rental Property Viewer",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Apply custom CSS for better styling
st.markdown("""
<style>
    .dataframe {
        font-size: 12px;
    }
    .st-emotion-cache-1wrcr25 {
        overflow-x: auto;
    }
    .price-increase {
        color: red;
        font-weight: bold;
    }
    .price-decrease {
        color: green;
        font-weight: bold;
    }
    .price-same {
        color: gray;
    }
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        margin-bottom: 1rem;
    }
    .subheader {
        font-size: 1.5rem;
        font-weight: bold;
        margin-top: 2rem;
        margin-bottom: 1rem;
    }
    .available {
        background-color: rgba(0, 255, 0, 0.1);
    }
    .unavailable {
        background-color: rgba(255, 0, 0, 0.1);
    }
</style>
""", unsafe_allow_html=True)

# Database connection using context manager
@st.cache_resource
def get_connection():
    # Check if running in Docker or directly
    if os.path.exists('/persist/rents.db'):
        db_path = '/persist/rents.db'
    else:
        db_path = 'rents.db'

    return sqlite3.connect(db_path, check_same_thread=False)

# Load data from database
@st.cache_data(ttl=300)  # Cache data for 5 minutes
def load_listings_data():
    conn = get_connection()
    query = """
    SELECT * FROM listings
    """
    df = pd.read_sql_query(query, conn)

    # Convert string boolean columns to actual booleans
    for col in ['gym', 'pool', 'parking', 'ev_charging', 'still_published']:
        if col in df.columns:
            df[col] = df[col].map({'True': True, 'False': False})

    # Convert date columns to datetime
    date_columns = ['available_on', 'last_updated', 'posted_on']
    for col in date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce', utc=True)

    return df

@st.cache_data(ttl=300)
def load_prices_data():
    conn = get_connection()
    query = """
    SELECT * FROM prices
    """
    df = pd.read_sql_query(query, conn)

    # Convert date columns to datetime
    if 'last_updated' in df.columns:
        df['last_updated'] = pd.to_datetime(df['last_updated'], errors='coerce', utc=True)

    return df

# Include this at the beginning of your main function or at the top of the script
def handle_property_selection():
    """Process property selection via query parameters or session state"""
    # This helps with preserving the selected property across refreshes
    if 'selected_property_id' not in st.session_state:
        st.session_state.selected_property_id = None

    # Check for URL query parameters (for direct links)
    params = st.experimental_get_query_params()
    if 'property_id' in params:
        st.session_state.selected_property_id = params['property_id'][0]
        # Redirect to remove the query param to avoid issues with refreshes
        st.experimental_set_query_params()

# Get price history for a specific listing
def get_price_history(listing_id):
    conn = get_connection()
    query = f"""
    SELECT last_updated, price
    FROM prices
    WHERE listing_id = '{listing_id}'
    ORDER BY last_updated
    """
    df = pd.read_sql_query(query, conn)
    df['last_updated'] = pd.to_datetime(df['last_updated'], errors='coerce', utc=True)
    return df

def get_property_price_history(listing_id):
    price_history = get_price_history(listing_id)

    dates  = price_history['last_updated'].tolist()
    prices = price_history['price'].tolist()

    return pd.DataFrame({'date': dates, 'price': prices})

# Calculate price change trends
def calculate_price_trends(listings_df, prices_df):
    # Group by listing_id and get min, max prices
    price_stats = prices_df.groupby('listing_id').agg(
        initial_price=('price', 'first'),
        current_price=('price', 'last'),
        price_count=('price', 'count')
    ).reset_index()

    # Merge with listings
    df = listings_df.merge(price_stats, left_on='id', right_on='listing_id', how='left')

    # Calculate price change
    df['price_change'] = df['current_price'] - df['initial_price']
    df['price_change_pct'] = ((df['current_price'] - df['initial_price']) / df['initial_price'] * 100).round(1)

    # Price trend direction
    df['trend'] = 'stable'
    df.loc[df['price_change'] > 0, 'trend'] = 'increase'
    df.loc[df['price_change'] < 0, 'trend'] = 'decrease'

    return df

# Get aggregated rental statistics
def get_rental_statistics(listings_df):
    # Filter for listings that are no longer published (were rented out)
    rented_df = listings_df[listings_df['still_published'] == False].copy()

    if rented_df.empty:
        return None

    # Group by number of rooms
    stats = rented_df.groupby('rooms').agg(
        avg_price=('last_price', 'mean'),
        min_price=('last_price', 'min'),
        max_price=('last_price', 'max'),
        count=('id', 'count')
    ).reset_index()

    return stats

# Format dollar amount
def format_price(price):
    if pd.isna(price):
        return "N/A"
    return f"${int(price):,}"

# Make URL clickable
def make_clickable(link):
    # Extract domain and path for display purposes
    parsed = urlparse(link)
    display_text = f"{parsed.netloc}{parsed.path[:20]}..."
    return f'<a target="_blank" rel="noreferrer href="{link}">{display_text}</a>'

def create_price_trend(row):
    """Create a price trend with history tooltip"""
    try:
        # Get the price change percentage
        percentage = row.get('price_change_pct', 0)

        # Determine the trend symbol based on the actual percentage value
        # This ensures correct arrow direction
        if percentage > 0:
            trend_symbol = "↑"
            color = "red"  # Price increases are typically red
        elif percentage < 0:
            trend_symbol = "↓"
            color = "green"  # Price decreases are typically green
        else:
            trend_symbol = "→"
            color = "gray"

        # Format the percentage (ensure it's displayed as absolute value with sign)
        formatted_pct = f"{abs(percentage):.1f}%"

        # Get price history for this property
        history = get_property_price_history(row.get('id'))

        if history.empty or len(history) <= 1:
            # No history or only one price point
            return f'<span style="color: {color};">{trend_symbol} {formatted_pct}</span>'
        else:
            # Sort the history by date to ensure chronological order
            history = history.sort_values(by='date')

            # Format price history as a chronological string: price1->price2->price3
            price_changes = "→".join([f"${p:,.0f}" for p in history['price']])

            property_id = row.get('id')

            # Create a clean HTML link with proper styling
            clean_html = f"""<span style="color: {color}; text-decoration: none; font-weight: bold;"
            >{trend_symbol} {formatted_pct}</span> {price_changes}"""

            # Remove all newlines to prevent them from appearing in the output
            clean_html = clean_html.replace('\n', ' ').strip()
            return clean_html

    except Exception as e:
        # Return a safe fallback if anything goes wrong
        print(f"Error creating price trend: {e}")
        return "→ 0%"

# Main application
def main():
    st.markdown('<div class="main-header">Rental Property Viewer</div>', unsafe_allow_html=True)

    # Load data
    with st.spinner('Loading data...'):
        listings_df = load_listings_data()
        prices_df = load_prices_data()

    # Calculate trends and statistics
    if not listings_df.empty and not prices_df.empty:
        listings_with_trends = calculate_price_trends(listings_df, prices_df)
        rental_stats = get_rental_statistics(listings_df)
    else:
        st.error("No data available. Please make sure the scraper has run at least once.")
        return

    # Sidebar filters
    st.sidebar.title("Filters")

    # Show available only toggle
    show_available_only = st.sidebar.checkbox("Show available properties only", value=True)

    # Price range filter
    min_price = int(listings_df['last_price'].min()) if not listings_df.empty else 0
    max_price = int(listings_df['last_price'].max()) if not listings_df.empty else 5000
    price_range = st.sidebar.slider(
        "Price Range",
        min_price,
        max_price,
        (min_price, max_price)
    )

    # Room filter
    available_rooms = sorted(listings_df['rooms'].unique().tolist())
    selected_rooms = st.sidebar.multiselect(
        "Number of Rooms",
        options=available_rooms,
        default=available_rooms
    )

    # Features filter
    col1, col2, col3, col4 = st.sidebar.columns(4)
    with col1:
        has_gym = st.checkbox("Gym")
    with col2:
        has_pool = st.checkbox("Pool")
    with col3:
        has_parking = st.checkbox("Parking")
    with col4:
        ev_charging = st.checkbox("EV Charging")

    # Apply filters
    filtered_df = listings_with_trends.copy()
    if show_available_only:
        filtered_df = filtered_df[filtered_df['still_published'] == True]

    filtered_df = filtered_df[
        (filtered_df['last_price'] >= price_range[0]) &
        (filtered_df['last_price'] <= price_range[1])
    ]

    if selected_rooms:
        filtered_df = filtered_df[filtered_df['rooms'].isin(selected_rooms)]

    if has_gym:
        filtered_df = filtered_df[filtered_df['gym'] == True]

    if has_pool:
        filtered_df = filtered_df[filtered_df['pool'] == True]

    if has_parking:
        filtered_df = filtered_df[filtered_df['parking'] == True]

    if ev_charging:
        filtered_df = filtered_df[filtered_df['ev_charging'] == True]

    # Main content area - Tabs
    tab1, tab2, tab3 = st.tabs(["Available Properties", "Price History", "Market Statistics"])

    with tab1:
        st.markdown('<div class="subheader">Available Properties</div>', unsafe_allow_html=True)
        if filtered_df.empty:
            st.warning("No properties match your filters.")
        else:
            # Prepare display dataframe
            display_df = filtered_df.copy()

            # Format columns for display
            display_df['last_price'] = display_df['last_price'].apply(format_price)
            display_df['size'] = display_df['size']
            display_df['available_on'] = display_df['available_on'].dt.strftime('%Y-%m-%d')
            display_df['posted_on'] = display_df['posted_on'].dt.strftime('%Y-%m-%d')
            display_df['last_updated'] = display_df['last_updated'].dt.strftime('%Y-%m-%d')

            # Create clickable title (instead of URL)
            display_df['clickable_title'] = display_df.apply(
                lambda x: f'<a href="{x["link"]}" target="_blank">{x["title"]}</a>', axis=1
            )

            # Add trend indicator with price history
            display_df['price_trend'] = display_df.apply(
                lambda x: create_price_trend(x), axis=1
            )

            # Select columns to display
            columns_to_display = [
                'clickable_title', 'rooms', 'size', 'last_price', 'price_trend',
                'available_on', 'posted_on', 'distance', 'gym', 'pool', 'parking', 'ev_charging'
            ]

            display_df = display_df[columns_to_display].rename(columns={
                'last_price': 'Price',
                'rooms': 'Rooms',
                'size': 'Size',
                'available_on': 'Available On',
                'posted_on': 'Posted On',
                'clickable_title': 'Title',
                'distance': 'Distance (km)',
                'gym': 'Gym',
                'pool': 'Pool',
                'parking': 'Parking',
                'ev_charging': 'EV Charging',
                'price_trend': 'Price Trend'
            })

            # Let user sort by any column
            sort_col = st.selectbox("Sort by", options=display_df.columns, index=3) # Default to Price
            sort_order = st.radio("Order", options=["Ascending", "Descending"], horizontal=True, index=0)

            # Apply sorting
            ascending = sort_order == "Ascending"
            sorted_df = display_df.sort_values(by=sort_col, ascending=ascending)

            # Display the dataframe without showing the index column
            html_df = sorted_df.to_html(escape=False, index=False)
            st.write(html_df, unsafe_allow_html=True)
            st.write(f"Showing {len(filtered_df)} properties")

    with tab2:
        st.markdown('<div class="subheader">Property Price History</div>', unsafe_allow_html=True)

        # Property selector
        selected_property_id = None
        property_options = ["Select a property..."] + listings_df['title'].tolist()

        selected_title = st.selectbox(
            "Select a property to view its price history:",
            options=property_options,
            index=0
        )

        # If a property was selected from the dropdown, use that instead
        if selected_title != "Select a property...":
            selected_property = listings_df[listings_df['title'] == selected_title].iloc[0]
            selected_property_id = selected_property['id']

        # Display price history if a property is selected
        if selected_property_id:
            # Get the property's details
            property_row = listings_df[listings_df['id'] == selected_property_id].iloc[0]
            title = property_row['title']

            st.write(f"### Price History for {title}")

            # Get price history data
            price_history = get_property_price_history(selected_property_id)

            if price_history.empty:
                st.info("No price history available for this property.")
            else:
                # Sort history by date
                price_history = price_history.sort_values(by='date')

                # Calculate price changes and percentages
                price_history['previous_price'] = price_history['price'].shift(1)
                price_history['price_change'] = price_history['price'] - price_history['previous_price']
                price_history['price_change_pct'] = (price_history['price_change'] / price_history['previous_price']) * 100

                # Drop the first row as it won't have a previous price
                price_history = price_history.dropna()

                # Display the price history table
                formatted_history = price_history.copy()
                formatted_history['date'] = formatted_history['date'].dt.strftime('%Y-%m-%d')
                formatted_history['price'] = formatted_history['price'].apply(lambda x: f"${x:,.0f}")
                formatted_history['price_change'] = formatted_history['price_change'].apply(
                    lambda x: f"+${x:,.0f}" if x > 0 else f"-${abs(x):,.0f}" if x < 0 else "$0"
                )
                formatted_history['price_change_pct'] = formatted_history['price_change_pct'].apply(
                    lambda x: f"+{x:.2f}%" if x > 0 else f"{x:.2f}%" if x < 0 else "0.00%"
                )

                # Display only relevant columns
                display_columns = ['date', 'price', 'price_change', 'price_change_pct']
                st.dataframe(formatted_history[display_columns], use_container_width=True)

                # Create a price history chart
                if len(price_history) > 1:
                    fig = px.line(
                        price_history,
                        x='date',
                        y='price',
                        title=f'Price History for {title}',
                        markers=True
                    )
                    fig.update_layout(
                        xaxis_title="Date",
                        yaxis_title="Price ($)",
                        yaxis=dict(tickprefix="$"),
                        hovermode="x"
                    )
                    st.plotly_chart(fig, use_container_width=True)

                # Show additional statistics
                if len(price_history) > 1:
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric(
                            "Initial Price",
                            f"${price_history['price'].iloc[0]:,.0f}",
                            delta=None
                        )
                    with col2:
                        st.metric(
                            "Current Price",
                            f"${price_history['price'].iloc[-1]:,.0f}",
                            delta=f"{((price_history['price'].iloc[-1] - price_history['price'].iloc[0]) / price_history['price'].iloc[0] * 100):.2f}%"
                        )
                    with col3:
                        total_days = (price_history['date'].iloc[-1] - price_history['date'].iloc[0]).days
                        st.metric("Days on Market", f"{total_days}")
        else:
            st.info("Select a property to view its price history, or click on a price trend from the property list.")

    with tab3:
        st.markdown('<div class="subheader">Market Statistics</div>', unsafe_allow_html=True)

        # Create tabs within this tab
        stat_tab1, stat_tab2 = st.tabs(["Price Distribution", "Rented Properties"])

        with stat_tab1:
            # Price distribution by room count
            if not listings_df.empty:
                fig = px.box(
                    listings_with_trends,
                    x='rooms',
                    y='last_price',
                    title='Price Distribution by Room Count',
                    labels={'rooms': 'Number of Rooms', 'last_price': 'Price ($)'}
                )
                st.plotly_chart(fig, use_container_width=True)

                # Price statistics table
                price_stats = listings_with_trends.groupby('rooms').agg(
                    avg_price=('last_price', 'mean'),
                    median_price=('last_price', 'median'),
                    min_price=('last_price', 'min'),
                    max_price=('last_price', 'max'),
                    count=('id', 'count')
                ).reset_index()

                price_stats['avg_price'] = price_stats['avg_price'].round().astype(int).apply(format_price)
                price_stats['median_price'] = price_stats['median_price'].round().astype(int).apply(format_price)
                price_stats['min_price'] = price_stats['min_price'].astype(int).apply(format_price)
                price_stats['max_price'] = price_stats['max_price'].astype(int).apply(format_price)

                price_stats.columns = ['Rooms', 'Average Price', 'Median Price', 'Min Price', 'Max Price', 'Count']
                st.write(price_stats)

                # Price trend analysis
                st.subheader("Price Trend Analysis")
                trend_data = listings_with_trends.groupby('trend').size().reset_index(name='count')
                if not trend_data.empty:
                    fig = px.pie(
                        trend_data,
                        values='count',
                        names='trend',
                        title='Price Trend Distribution',
                        color='trend',
                        color_discrete_map={
                            'increase': 'red',
                            'decrease': 'green',
                            'stable': 'gray'
                        }
                    )
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("Not enough data to display price distribution.")

        with stat_tab2:
            # Statistics for rented properties
            if rental_stats is not None and not rental_stats.empty:
                st.subheader("Recently Rented Properties")

                # Format prices
                display_stats = rental_stats.copy()
                display_stats['avg_price'] = display_stats['avg_price'].round().astype(int).apply(format_price)
                display_stats['min_price'] = display_stats['min_price'].astype(int).apply(format_price)
                display_stats['max_price'] = display_stats['max_price'].astype(int).apply(format_price)

                display_stats.columns = ['Rooms', 'Average Price', 'Min Price', 'Max Price', 'Count']
                st.table(display_stats)

                # Show chart for rented properties
                rented_df = listings_df[listings_df['still_published'] == False].copy()
                fig = px.histogram(
                    rented_df,
                    x='last_price',
                    color='rooms',
                    title='Distribution of Rented Property Prices',
                    labels={'last_price': 'Price ($)', 'count': 'Number of Properties'},
                    nbins=20
                )
                st.plotly_chart(fig, use_container_width=True)

                # Time on market analysis
                if 'posted_on' in rented_df.columns and 'last_updated' in rented_df.columns:
                    rented_df['days_on_market'] = (rented_df['last_updated'] - rented_df['posted_on']).dt.days
                    fig = px.box(
                        rented_df,
                        x='rooms',
                        y='days_on_market',
                        title='Days on Market by Room Count',
                        labels={'rooms': 'Number of Rooms', 'days_on_market': 'Days on Market'}
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # Average days on market
                    avg_days = rented_df.groupby('rooms')['days_on_market'].mean().round(1).reset_index()
                    avg_days.columns = ['Rooms', 'Average Days on Market']
                    st.write("Average Days on Market by Room Count")
                    st.write(avg_days)
            else:
                st.info("No data available for rented properties yet. This section will populate as properties are rented out.")

# CSS for styling
def load_css():
    st.markdown("""
    <style>
        .main-header {
            font-size: 2.5rem;
            font-weight: 700;
            color: #1E88E5;
            margin-bottom: 1rem;
            text-align: center;
        }
        .subheader {
            font-size: 1.8rem;
            font-weight: 600;
            color: var(--text-color);
            margin-bottom: 1rem;
            border-bottom: 1px solid var(--secondary-background-color);
            padding-bottom: 0.5rem;
        }

        /* Retain alternating table rows but with dark mode compatibility */
        table {
            width: 100%;
            border-collapse: collapse;
        }

        /* Fix for tables in both modes */
        .stDataFrame {
            color: var(--text-color);
        }
        .stDataFrame tbody tr:nth-child(even) {
            background-color: rgba(128, 128, 128, 0.1);
        }
        .stDataFrame tbody tr:nth-child(odd) {
            background-color: rgba(128, 128, 128, 0.0);
        }
        .stDataFrame th {
            background-color: #1E88E5;
            color: white !important;
            text-align: left;
            font-weight: bold;
        }

        /* Default tables (not in DataFrames) */
        tr:nth-child(even) {
            background-color: rgba(128, 128, 128, 0.1);
        }

        /* Tab styling with better dark mode support */
        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px;
            white-space: pre-wrap;
            background-color: var(--secondary-background-color);
            border-radius: 4px;
            padding: 0px 16px;
            font-weight: 600;
            color: var(--text-color);
        }
        .stTabs [aria-selected="true"] {
            background-color: #1E88E5 !important;
            color: white !important;
        }

        /* Filter buttons and widgets - dark mode compatibility */
        .stButton>button {
            color: var(--text-color);
            background-color: var(--secondary-background-color);
            border: 1px solid var(--secondary-background-color);
        }
        .stButton>button:hover {
            color: var(--text-color);
            background-color: var(--primary-background-color);
            border: 1px solid #1E88E5;
        }
        .stButton [data-baseweb="button"][aria-selected="true"] {
            background-color: #1E88E5 !important;
            color: white !important;
        }

        /* Style for clickable title */
        .clickable-title {
            text-decoration: none;
            color: #1E88E5;
            font-weight: bold;
        }
        .clickable-title:hover {
            text-decoration: underline;
        }
    </style>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    load_css()
    main()