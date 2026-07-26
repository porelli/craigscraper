import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime, timedelta
from urllib.parse import urlparse
import os
import sys

# `streamlit run ui/ui.py` puts this script's directory (ui/) on sys.path, NOT the repo root,
# so the craigscraper package isn't importable by default. Add the repo root (this file's
# parent's parent) so the import works regardless of the launch directory (incl. the container).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from craigscraper.market_analysis import (
    monthly_median_rent, new_listings_per_month, pct_change_vs, active_listings_per_month
)

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

@st.cache_data(ttl=300)
def load_price_months():
    # one row per price point: month, bedrooms, price (joined to the listing's bedroom count).
    # Timestamps are stored ISO-8601 with a 'T' and tz offset (e.g. 2025-02-16T12:00:53-0800),
    # which SQLite strftime() CANNOT parse (returns NULL). substr(...,1,7) extracts 'YYYY-MM'
    # directly from the ISO string, which is correct for that fixed layout.
    conn = get_connection()
    query = """
    SELECT substr(p.last_updated, 1, 7) AS month, l.bedrooms AS bedrooms, p.price AS price
    FROM prices p
    JOIN listings l ON l.id = p.listing_id
    WHERE l.bedrooms IS NOT NULL AND p.price IS NOT NULL
    """
    return pd.read_sql_query(query, conn)

@st.cache_data(ttl=300)
def load_posted_and_dom():
    # posted month (for new-listings) and approximate days-on-market per listing.
    # substr(...,1,7) is used instead of strftime() because the ISO-8601 timestamps with a
    # 'T'/tz offset are not parseable by SQLite's date functions (see load_price_months).
    conn = get_connection()
    query = """
    SELECT substr(posted_on, 1, 7) AS posted_month,
           substr(last_updated, 1, 7) AS last_month,
           posted_on, last_updated
    FROM listings
    WHERE posted_on IS NOT NULL
    """
    df = pd.read_sql_query(query, conn)
    df['posted_on'] = pd.to_datetime(df['posted_on'], errors='coerce', utc=True)
    df['last_updated'] = pd.to_datetime(df['last_updated'], errors='coerce', utc=True)
    df['days_on_market'] = (df['last_updated'] - df['posted_on']).dt.days
    return df

# Get price history for a specific listing
def get_price_history(listing_id):
    conn = get_connection()
    query = """
    SELECT last_updated, price
    FROM prices
    WHERE listing_id = ?
    ORDER BY last_updated
    """
    df = pd.read_sql_query(query, conn, params=(listing_id,))
    df['last_updated'] = pd.to_datetime(df['last_updated'], errors='coerce', utc=True)
    return df

def get_property_price_history(listing_id):
    price_history = get_price_history(listing_id)

    dates  = price_history['last_updated'].tolist()
    prices = price_history['price'].tolist()

    return pd.DataFrame({'date': dates, 'price': prices})

@st.cache_data(ttl=300)
def get_listing_timeline(listing_id):
    conn = get_connection()
    events = []

    # price events: chronological, collapse consecutive identical prices (display-only)
    prices = pd.read_sql_query(
        "SELECT last_updated, price FROM prices WHERE listing_id = ? ORDER BY last_updated",
        conn, params=(listing_id,)
    )
    prev = None
    for i, r in prices.iterrows():
        if prev is None:
            events.append({'when': r['last_updated'], 'field': 'price', 'old': '', 'new': f"${int(r['price']):,}"})
        elif r['price'] != prev:
            events.append({'when': r['last_updated'], 'field': 'price', 'old': f"${int(prev):,}", 'new': f"${int(r['price']):,}"})
        prev = r['price']

    # field changes
    changes = pd.read_sql_query(
        "SELECT changed_at, field, old_value, new_value FROM listing_changes WHERE listing_id = ? ORDER BY changed_at",
        conn, params=(listing_id,)
    )
    for _, r in changes.iterrows():
        events.append({'when': r['changed_at'], 'field': r['field'], 'old': r['old_value'], 'new': r['new_value']})

    df = pd.DataFrame(events)
    if not df.empty:
        df['when'] = pd.to_datetime(df['when'], errors='coerce', utc=True)
        df = df.sort_values(by='when', ascending=False)
    return df

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
            return (f'<a href="?history={row.get("id")}" target="_self" '
                    f'style="color: {color}; text-decoration: none;">{trend_symbol} {formatted_pct}</a>')
        else:
            # Sort the history by date to ensure chronological order
            history = history.sort_values(by='date')

            # Collapse consecutive identical prices so unchanged re-scrapes don't render
            # as fake steps (e.g. $2,160→$2,160). Keeps only real transitions.
            collapsed = []
            for p in history['price']:
                if not collapsed or collapsed[-1] != p:
                    collapsed.append(p)

            # Format price history as a chronological string: price1->price2->price3
            price_changes = "→".join([f"${p:,.0f}" for p in collapsed])

            property_id = row.get('id')

            # Link the trend to open the timeline modal via query param
            clean_html = (
                f'<a href="?history={property_id}" target="_self" '
                f'style="color: {color}; text-decoration: none; font-weight: bold;">'
                f'{trend_symbol} {formatted_pct}</a> {price_changes}'
            )
            return clean_html

    except Exception as e:
        # Return a safe fallback if anything goes wrong
        print(f"Error creating price trend: {e}")
        return "→ 0%"

@st.dialog("Listing timeline")
def show_timeline_dialog(listing_id, title):
    st.write(f"**{title}**")
    df = get_listing_timeline(listing_id)
    if df.empty:
        st.info("No recorded changes yet.")
        return
    disp = df.copy()
    disp['when'] = disp['when'].dt.strftime('%Y-%m-%d')
    disp['change'] = disp['old'].fillna('').astype(str) + ' → ' + disp['new'].fillna('').astype(str)
    st.dataframe(
        disp[['when', 'field', 'change']].rename(columns={'when': 'When', 'field': 'What', 'change': 'Change'}),
        width="stretch", hide_index=True
    )

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
    else:
        st.error("No data available. Please make sure the scraper has run at least once.")
        return

    # open the timeline modal if navigated via ?history=<id>
    hist_param = st.query_params.get("history")
    if hist_param:
        try:
            hist_id = int(hist_param)
            match = listings_df[listings_df['id'] == hist_id]
            title = match.iloc[0]['title'] if not match.empty else str(hist_id)
            show_timeline_dialog(hist_id, title)
        except (ValueError, TypeError):
            pass
        finally:
            st.query_params.clear()

    # Sidebar filters
    st.sidebar.title("Filters")

    # Show available only toggle
    show_available_only = st.sidebar.checkbox("Show available properties only", value=True)

    # Price range filter
    min_price = int(listings_df['last_price'].min()) if not listings_df.empty else 0
    max_price = int(listings_df['last_price'].max()) if not listings_df.empty else 5000
    if min_price >= max_price:
        # a slider requires min < max; with a single distinct price there's nothing to range over
        st.sidebar.write(f"Price: ${min_price:,}")
        price_range = (min_price, max_price)
    else:
        price_range = st.sidebar.slider(
            "Price Range",
            min_price,
            max_price,
            (min_price, max_price)
        )

    # Bedroom / bathroom filters (numeric, from the parsed columns)
    bed_vals = sorted(v for v in listings_df['bedrooms'].dropna().unique())
    bath_vals = sorted(v for v in listings_df['bathrooms'].dropna().unique())
    selected_beds = st.sidebar.multiselect(
        "Bedrooms",
        options=bed_vals,
        default=bed_vals,
        format_func=lambda x: f"{int(x)}" if float(x).is_integer() else f"{x}"
    )
    selected_baths = st.sidebar.multiselect(
        "Bathrooms",
        options=bath_vals,
        default=bath_vals,
        format_func=lambda x: f"{int(x)}" if float(x).is_integer() else f"{x}"
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

    if selected_beds:
        filtered_df = filtered_df[filtered_df['bedrooms'].isin(selected_beds)]
    if selected_baths:
        filtered_df = filtered_df[filtered_df['bathrooms'].isin(selected_baths)]

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

            # Render feature flags as emoji (values are Python booleans from load_listings_data).
            # fillna(False) guards against any NULL feature value rendering as literal 'nan'.
            feature_icons = {'gym': '🏋️', 'pool': '🏊', 'parking': '🅿️', 'ev_charging': '⚡'}
            for col, icon in feature_icons.items():
                display_df[col] = display_df[col].fillna(False).map({True: icon, False: ''})

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
                'clickable_title', 'bedrooms', 'bathrooms', 'size', 'last_price', 'price_trend',
                'available_on', 'posted_on', 'distance', 'gym', 'pool', 'parking', 'ev_charging'
            ]

            display_df = display_df[columns_to_display].rename(columns={
                'last_price': 'Price',
                'bedrooms': 'Bedrooms',
                'bathrooms': 'Bathrooms',
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

            # Let user sort by any column. Default to Size, descending.
            # NOTE: 'Price' sorts lexically here because it's a formatted string ($2,100) at
            # this point — a pre-existing caveat, intentionally not fixed in this change.
            sort_options = list(display_df.columns)
            default_sort_index = sort_options.index('Size') if 'Size' in sort_options else 0
            sort_col = st.selectbox("Sort by", options=sort_options, index=default_sort_index)
            sort_order = st.radio("Order", options=["Ascending", "Descending"], horizontal=True, index=1)

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
                st.dataframe(formatted_history[display_columns], width="stretch")

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
                    st.plotly_chart(fig, width="stretch")

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
        rent_tab, activity_tab, snapshot_tab = st.tabs(["Rent over time", "Market activity", "Snapshot"])

        # ---- Rent over time ----
        with rent_tab:
            price_months = load_price_months()
            medians = monthly_median_rent(price_months)
            if medians.empty:
                st.info("Not enough price history yet to chart rent over time.")
            else:
                fig = px.line(
                    medians, x='month', y='median_price', color='bedrooms', markers=True,
                    title='Median asking rent by month (per bedroom count)',
                    labels={'month': 'Month', 'median_price': 'Median rent ($)', 'bedrooms': 'Bedrooms'}
                )
                fig.update_layout(yaxis=dict(tickprefix="$"), hovermode="x")
                st.plotly_chart(fig, width="stretch")

                # overall median per month (all bedrooms pooled) for the delta metrics
                overall = (price_months.groupby('month')['price'].median()
                           .reset_index().rename(columns={'price': 'median_price'})
                           .sort_values('month'))
                c3, c6, c12 = st.columns(3)
                for col, months_back, label in ((c3, 3, "vs 3 mo ago"),
                                                 (c6, 6, "vs 6 mo ago"),
                                                 (c12, 12, "vs 12 mo ago")):
                    pct = pct_change_vs(overall, months_back)
                    latest = overall.iloc[-1]['median_price']
                    with col:
                        st.metric(label, f"${latest:,.0f}",
                                  delta=(f"{pct:+.1f}%" if pct is not None else "n/a"))

        # ---- Market activity ----
        with activity_tab:
            pdom = load_posted_and_dom()
            if pdom.empty:
                st.info("Not enough listing data yet to chart market activity.")
            else:
                newpm = new_listings_per_month(pdom['posted_month'])
                if not newpm.empty:
                    fig = px.bar(newpm, x='month', y='count', title='New listings per month',
                                 labels={'month': 'Month', 'count': 'New listings'})
                    st.plotly_chart(fig, width="stretch")

                # inventory over time: active listings per month (posted_month..last_month inclusive)
                inv = active_listings_per_month(pdom[['posted_month', 'last_month']])
                if not inv.empty:
                    fig = px.line(inv, x='month', y='active', markers=True,
                                  title='Active listings per month (inventory, approximate)',
                                  labels={'month': 'Month', 'active': 'Active listings'})
                    st.plotly_chart(fig, width="stretch")
                    st.caption("Inventory is approximate: a listing is counted as active from its "
                               "posted month through its last-seen month.")

                # approximate days-on-market by posted month
                dom = (pdom.dropna(subset=['days_on_market'])
                       .groupby('posted_month')['days_on_market'].mean().reset_index())
                if not dom.empty:
                    fig = px.line(dom, x='posted_month', y='days_on_market', markers=True,
                                  title='Average days on market by posted month (approximate)',
                                  labels={'posted_month': 'Month', 'days_on_market': 'Avg days on market'})
                    st.plotly_chart(fig, width="stretch")
                    st.caption("Days on market is approximate: measured from posted date to the "
                               "listing's last-seen date (we detect removal at the next scrape).")

        # ---- Snapshot (existing charts, regrouped by bedrooms) ----
        with snapshot_tab:
            snap_price, snap_rented = st.tabs(["Price Distribution", "Rented Properties"])
            with snap_price:
                if not listings_df.empty:
                    fig = px.box(listings_with_trends, x='bedrooms', y='last_price',
                                 title='Price Distribution by Bedrooms',
                                 labels={'bedrooms': 'Bedrooms', 'last_price': 'Price ($)'})
                    st.plotly_chart(fig, width="stretch")

                    price_stats = listings_with_trends.groupby('bedrooms').agg(
                        avg_price=('last_price', 'mean'),
                        median_price=('last_price', 'median'),
                        min_price=('last_price', 'min'),
                        max_price=('last_price', 'max'),
                        count=('id', 'count')
                    ).reset_index()
                    for c in ['avg_price', 'median_price', 'min_price', 'max_price']:
                        price_stats[c] = price_stats[c].round().astype(int).apply(format_price)
                    price_stats.columns = ['Bedrooms', 'Average Price', 'Median Price', 'Min Price', 'Max Price', 'Count']
                    st.write(price_stats)

                    st.subheader("Price Trend Analysis")
                    trend_data = listings_with_trends.groupby('trend').size().reset_index(name='count')
                    if not trend_data.empty:
                        fig = px.pie(trend_data, values='count', names='trend',
                                     title='Price Trend Distribution', color='trend',
                                     color_discrete_map={'increase': 'red', 'decrease': 'green', 'stable': 'gray'})
                        st.plotly_chart(fig, width="stretch")
                else:
                    st.warning("Not enough data to display price distribution.")

            with snap_rented:
                rented_df = listings_df[listings_df['still_published'] == False].copy()
                if rented_df.empty:
                    st.info("No data available for rented properties yet.")
                else:
                    rented_stats = rented_df.groupby('bedrooms').agg(
                        avg_price=('last_price', 'mean'),
                        min_price=('last_price', 'min'),
                        max_price=('last_price', 'max'),
                        count=('id', 'count')
                    ).reset_index()
                    for c in ['avg_price', 'min_price', 'max_price']:
                        rented_stats[c] = rented_stats[c].round().astype(int).apply(format_price)
                    rented_stats.columns = ['Bedrooms', 'Average Price', 'Min Price', 'Max Price', 'Count']
                    st.table(rented_stats)

                    fig = px.histogram(rented_df, x='last_price', color='bedrooms',
                                       title='Distribution of Rented Property Prices',
                                       labels={'last_price': 'Price ($)'}, nbins=20)
                    st.plotly_chart(fig, width="stretch")

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